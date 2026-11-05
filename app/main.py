"""FastAPI-App: statisches Frontend, Sitzungsverwaltung, Audio-WebSocket.

Läuft lokal auf localhost - damit ist der Secure Context für getUserMedia
ohne Zertifikat erfüllt. Auf einem Server braucht es dafür HTTPS.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
import re
from urllib.parse import quote, unquote

import httpx
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import asyncio as _asyncio

from app import localconfig, models, usage, userprefs, vault
from app.auth import IMPLICIT_USER, engines_from_groups
from app.config import (PRESETS, Endpoint, Engine, Settings, SummaryModel,
                        default_engine, settings)
from app.version import __version__, is_newer
from app.llm import AllEndpointsFailed, ChatClient, LLMError
from app.output_types import load_output_types
from app.protocol.strategies import STRATEGIES
from app.session import LiveSession
from app import summary
from app.docx_export import markdown_to_docx
from app.transcription import (
    StreamingTranscriptionClient,
    TranscriptionError,
    create_from_engine,
    paragraphs,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

sessions: dict[str, LiveSession] = {}
# Wem gehört welche Sitzung. Die IDs sind zufällig, aber im gehosteten Betrieb
# darf sich niemand mit einer fremden ID an eine fremde Sitzung hängen.
owners: dict[str, str] = {}


# Freigaben je Benutzer aus der letzten Remote-Groups-Kopfzeile (nur bei
# AUTH_PROXY). Jede Anfrage bringt sie neu mit; eine beim Proxy entzogene
# Freigabe gilt also ab der nächsten Anfrage.
proxy_engines: dict[str, list[str]] = {}
proxy_groups: dict[str, set[str]] = {}

# Wer die Verbrauchsübersicht sehen darf.
ADMIN_GROUP = "admins"
# Diese Freigabe schaltet alle gesperrten Dienste und Modelle auf einmal frei.
ALLE = "alle"


def _user_from_proxy(headers) -> str | None:
    name = (headers.get("remote-user") or "").strip()
    if not name:
        return None
    kopf = headers.get("remote-groups")
    proxy_engines[name] = engines_from_groups(kopf)
    proxy_groups[name] = {g.strip() for g in (kopf or "").split(",") if g.strip()}
    return name


def _darf(eintrag_id: str, restricted: bool, user: str | None) -> bool:
    """Gesperrte Einträge nur mit Freigabe aus den Gruppen lp-<kennung> - und
    nur hinter dem Proxy. Der implizite Benutzer bezahlt die Schlüssel
    selbst, für ihn gibt es keine fremde Freigabe."""
    if not restricted or not settings.auth_proxy:
        return True
    erlaubt = set(proxy_engines.get(user or "", []))
    return eintrag_id in erlaubt or ALLE in erlaubt


def _is_admin(user: str) -> bool:
    return settings.auth_proxy and ADMIN_GROUP in proxy_groups.get(user, set())


async def current_user(request: Request) -> str:
    """Wer fragt.

    Ohne Proxy gibt es genau einen Benutzer, am eigenen Rechner wie auf dem
    Server: keine Anmeldung, keine Grenze. Wer den Dienst offen ins Netz
    stellt, schützt ihn davor (Proxy mit Anmeldung, VPN) oder schaltet
    AUTH_PROXY ein."""
    if not settings.auth_proxy:
        return IMPLICIT_USER
    # Ohne Kopfzeile kam die Anfrage nicht über den Proxy - dann ist er
    # falsch eingerichtet. Geschlossen ausfallen.
    name = _user_from_proxy(request.headers)
    if not name:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich")
    return name


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.hosted and not settings.auth_proxy:
        logger.warning("Server-Betrieb ohne Anmeldung: Jeder, der den Dienst erreicht, "
                       "nutzt die hinterlegten Schlüssel. Nur hinter einem Schutz "
                       "betreiben oder AUTH_PROXY einschalten.")
    app.state.output_types = load_output_types()
    endpunkte = _llm_endpoints()
    if not endpunkte:
        logger.warning("Kein Modell eingerichtet - MISTRAL_API_KEY oder einen "
                       "OpenAI-kompatiblen Anbieter in die .env eintragen.")
    health = await ChatClient(endpunkte).health() if endpunkte else []
    for entry in health:
        if entry["ok"]:
            logger.info("LLM-Endpunkt %s erreichbar (%s)", entry["name"], entry["model"])
        else:
            logger.warning("LLM-Endpunkt %s NICHT erreichbar: %s", entry["name"], entry["error"])
    reaper = asyncio.create_task(_reap_forever())
    yield
    reaper.cancel()
    with suppress(asyncio.CancelledError):
        await reaper


def reap_sessions(now: float | None = None) -> list[str]:
    """Sitzungen entfernen, die nicht laufen und lange niemand angefasst hat.

    Nicht laufend heißt: beendet, oder angelegt und nie verbunden. Die Frist
    zählt ab der letzten Nutzung, nicht ab dem Stopp - wer nach dem Termin
    noch exportiert, soll die Sitzung nicht unter der Hand verlieren."""
    now = time.monotonic() if now is None else now
    frist = settings.session_ttl_minutes * 60
    weg = [sid for sid, s in list(sessions.items())
           if not s.running and now - s.last_activity > frist]
    for sid in weg:
        sessions.pop(sid, None)
        owners.pop(sid, None)
    if weg:
        logger.info("%d beendete Sitzung(en) aus dem Speicher entfernt", len(weg))
    return weg


async def _reap_forever() -> None:
    runde = 0
    while True:
        await asyncio.sleep(60)
        reap_sessions()
        if runde % (24 * 60) == 0 and settings.auth_proxy:
            # Einmal am Tag: Verbrauch älter als 13 Monate löschen. Für die
            # Kostenkontrolle reicht ein Jahr zum Vergleich, länger braucht
            # es die Zeilen nicht.
            usage.prune(settings.usage_dir, keep_months=13)
        runde += 1


async def stop_if_orphaned(session: LiveSession, grace: float | None = None) -> None:
    """Nach einem Verbindungsabbruch ohne Stopp: kurz auf eine neue
    Verbindung warten, dann geordnet beenden.

    Ohne das bliebe die Sitzung bis zur Zeitgrenze als laufend stehen, hielte
    die Verbindung zum Dienst offen und belegte einen Platz des Benutzers -
    und niemand sähe sie mehr."""
    await asyncio.sleep(settings.orphan_grace_seconds if grace is None else grace)
    if session.running and session.listeners == 0:
        logger.info("Sitzung %s ohne Verbindung, wird beendet", session.id)
        await session.stop()


app = FastAPI(title="Live-Protokoll", lifespan=lifespan)


@app.get("/")
async def index(user: str = Depends(current_user)) -> FileResponse:
    return FileResponse(_static_file("index.html"),
                        headers={"Cache-Control": "no-cache"})


# -- Overlay ------------------------------------------------------------------
# Eigene Dateien einer Instanz (OVERLAY_DIR), ohne den Kern anzufassen. Was
# dort liegt, gewinnt gegen static/; zusätzliche HTML-Seiten werden unter
# /<name> erreichbar. Siehe docs/overlay.md.

# Dateien im Overlay, die keine eigene Seite sind.
OVERLAY_SPECIAL = {"index.html", "banner.html"}
SEITENNAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def _overlay() -> Path | None:
    pfad = settings.overlay_dir
    return pfad if pfad is not None and pfad.is_dir() else None


def _static_file(name: str) -> Path:
    """Eine Datei aus dem Overlay, wenn es sie dort gibt, sonst aus static/."""
    overlay = _overlay()
    if overlay is not None and (overlay / name).is_file():
        return overlay / name
    return STATIC_DIR / name


def _overlay_pages() -> list[dict]:
    """Zusätzliche Seiten für die Fußzeile, alphabetisch.

    Beschriftet wird mit dem <title> der Seite bis zum ersten Gedankenstrich,
    aus "Datenschutz – Live-Protokoll" wird "Datenschutz"."""
    overlay = _overlay()
    if overlay is None:
        return []
    seiten = []
    for datei in sorted(overlay.glob("*.html")):
        if datei.name in OVERLAY_SPECIAL or not SEITENNAME.fullmatch(datei.stem):
            continue
        try:
            text = datei.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        treffer = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        titel = html.unescape(treffer.group(1)).strip() if treffer else ""
        titel = re.split(r"\s+[–—|-]\s+", titel)[0].strip() or datei.stem.capitalize()
        seiten.append({"path": f"/{datei.stem}", "title": titel})
    return seiten


def _banner() -> str:
    """Inhalt der Hinweisleiste als HTML, leer heißt keine.

    banner.html aus dem Overlay kommt vom Betreiber der Instanz und darf
    deshalb Auszeichnung tragen; BANNER_TEXT ist Klartext."""
    overlay = _overlay()
    if overlay is not None and (overlay / "banner.html").is_file():
        return (overlay / "banner.html").read_text(encoding="utf-8").strip()
    return html.escape(settings.banner_text.strip())


@app.get("/api/config")
async def get_config(user: str = Depends(current_user)) -> dict:
    return {
        "output_types": [
            {"id": t.id, "name": t.name, "short_name": t.short_name,
             "description": t.description, "default_strategy": t.default_strategy,
             "summary_blocks": [{"id": b.id, "label": b.label, "default": b.default}
                                for b in t.summary_blocks]}
            for t in app.state.output_types.values()
        ],
        "summary_models": [
            {"id": m.id, "name": m.name, "label": m.label, "speed": m.speed,
             "cost": m.cost, "privacy": m.privacy_text}
            for m in _summary_models(user)
        ],
        "summary_details": list(summary.DETAIL),
        "strategies": list(STRATEGIES),
        "engines": [
            {"id": e.id, "name": e.name, "local": e.local,
             "streaming": e.streaming, "vocabulary": e.supports_vocabulary,
             "privacy": e.privacy_text, "cost": e.cost, "diarize": e.diarize,
             "configured": e.ready}
            for e in _engines(user)
        ],
        "llm_endpoints": [
            {"name": e.name, "label": e.display, "model": e.model, "local": e.local,
             "privacy": e.privacy_text, "cost": e.cost,
             "configured": e.ready}
            for e in _llm_endpoints(user)
        ],
        "defaults": {
            "trigger_seconds": settings.trigger_seconds,
            "trigger_chars": settings.trigger_chars,
            # Gehostet ohne Vorgabe: Jeder pflegt seine eigenen Begriffe.
            "vocabulary": "" if settings.hosted else settings.vocabulary,
            "engine": default_engine(_engines(user), settings.hosted),
            "llm_endpoint": "auto",
            "live_protocol": False,
            "summary_detail": summary.DEFAULT_DETAIL,
        },
        "sample_rate": 16000,
        "version": __version__,
        "mode": settings.deployment_mode,
        "user": user,
        "download_url": settings.download_url if settings.hosted else None,
        "pages": _overlay_pages(),
        "banner": _banner(),
        "consent_hint": settings.consent_hint,
        # Vorlagen für den OpenAI-kompatiblen Anbieter in den Einstellungen.
        "provider_presets": {k: {f: v[f] for f in ("name", "base_url", "live_model",
                                                    "summary_model", "transcription_model",
                                                    "privacy", "note")}
                             for k, v in PRESETS.items()},
        "upload_mb": settings.max_upload_mb,
        # Gespeicherte Sitzungen gibt es nur gehostet; am eigenen Rechner ist
        # die Projektdatei der Speicher.
        "vault": settings.hosted,
        "admin": _is_admin(user),
        "quota": _kontingent(user),
        "limits": {
            "session_minutes": settings.max_session_minutes,
            "sessions_per_user": settings.max_sessions_per_user
            if settings.auth_proxy else None,
        } if settings.hosted else None,
    }


# Zustand des Modell-Downloads. Der erste Start lädt 574 MB nach; ohne
# Rückmeldung im Fenster sähe das nach einem hängenden Programm aus, denn ein
# gebündeltes Programm hat keine sichtbare Konsole.
model_state: dict = {"running": False, "downloaded": 0, "error": None}


@app.get("/api/model")
async def get_model(user: str = Depends(current_user)) -> dict:
    return {
        "name": models.DEFAULT.name,
        "size": models.DEFAULT.size,
        "present": models.is_present(),
        "needed": not settings.hosted,
        **model_state,
    }


@app.post("/api/model")
async def start_model_download(user: str = Depends(current_user)) -> dict:
    if settings.hosted:
        raise HTTPException(404, "Im gehosteten Betrieb nicht verfügbar")
    if models.is_present() or model_state["running"]:
        return {"running": model_state["running"], "present": models.is_present()}

    model_state.update(running=True, downloaded=0, error=None)

    def fortschritt(done: int, total: int) -> None:
        model_state["downloaded"] = done

    async def lauf() -> None:
        try:
            await _asyncio.to_thread(models.ensure, models.DEFAULT, fortschritt)
        except Exception as exc:  # noqa: BLE001 - der Nutzer soll den Grund sehen
            logger.exception("Modell-Download fehlgeschlagen")
            model_state["error"] = str(exc)
        finally:
            model_state["running"] = False

    _asyncio.create_task(lauf())
    return {"running": True, "present": False}


@app.get("/api/update")
async def check_update(user: str = Depends(current_user)) -> dict:
    """Fragt die Update-Quelle. Antwortet immer, auch wenn sie nicht erreichbar
    ist - eine fehlgeschlagene Prüfung darf nichts blockieren."""
    ergebnis = {"current": __version__, "available": False, "checked": False}
    if settings.hosted or not settings.update_check or not settings.update_url:
        return ergebnis
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=4.0)) as client:
            antwort = await client.get(settings.update_url, follow_redirects=True,
                                       headers={"Accept": "application/vnd.github+json"})
        antwort.raise_for_status()
        daten = antwort.json()
        if not isinstance(daten, dict):
            raise ValueError("unerwartete Antwort")
    except Exception as exc:  # noqa: BLE001 - offline ist kein Fehlerfall
        logger.info("Update-Prüfung nicht möglich: %s", exc)
        ergebnis["error"] = str(exc)[:200]
        return ergebnis

    # GitHub nennt die Version als Tag ("v0.7.0") und die Seite des Releases
    # als html_url; eine eigene Quelle darf es schlichter halten.
    neueste = str(daten.get("tag_name") or daten.get("version") or "").lstrip("v")
    ergebnis.update(
        checked=True,
        latest=neueste,
        available=bool(neueste) and is_newer(neueste, __version__),
        download_url=daten.get("html_url") or daten.get("download_url")
        or settings.download_url,
        source=settings.update_url,
    )
    return ergebnis


@app.get("/api/prefs")
async def get_prefs(user: str = Depends(current_user)) -> dict:
    """Was dieser Benutzer zuletzt gewählt hat."""
    return userprefs.load(settings.user_prefs_dir, user)


@app.put("/api/prefs")
async def put_prefs(payload: dict, user: str = Depends(current_user)) -> dict:
    return userprefs.save(settings.user_prefs_dir, user, payload)


@app.get("/api/settings")
async def get_local_settings(user: str = Depends(current_user)) -> dict:
    """Lokale Einstellungen - Schlüssel kommen nie zurück, nur ob sie gesetzt sind."""
    if settings.hosted:
        raise HTTPException(404, "Im gehosteten Betrieb nicht verfügbar")
    return localconfig.redacted()


@app.put("/api/settings")
async def put_local_settings(payload: dict, user: str = Depends(current_user)) -> dict:
    """Schlüssel und OpenAI-kompatibler Anbieter der Desktop-Fassung.

    Ein Schlüssel, der nicht mitkommt, bleibt, wie er war - die Oberfläche
    bekommt ihn nie zurück und kann ihn deshalb auch nicht mitschicken."""
    if settings.hosted:
        raise HTTPException(404, "Im gehosteten Betrieb nicht verfügbar")
    unbekannt = set(payload) - {"mistral_api_key", "openai_compat"}
    if unbekannt:
        raise HTTPException(400, f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")
    patch = {}
    if "mistral_api_key" in payload:
        patch["mistral_api_key"] = str(payload["mistral_api_key"] or "").strip()
    if "openai_compat" in payload:
        neu = payload["openai_compat"]
        if neu is None:
            patch["openai_compat"] = None
        elif isinstance(neu, dict):
            unbekannt = set(neu) - set(localconfig.PROVIDER_FIELDS)
            if unbekannt:
                raise HTTPException(400, f"Unbekannte Felder: {', '.join(sorted(unbekannt))}")
            if neu.get("preset") and neu["preset"] not in PRESETS:
                raise HTTPException(400, f"Unbekannte Vorlage: {neu['preset']}")
            lokal = localconfig.load()
            # Ohne neuen Schlüssel gilt der bisherige - auch der aus den alten
            # Feldern, die der Anbieter jetzt ablöst.
            bisher = ((lokal.get("openai_compat") or {}).get("api_key")
                      or lokal.get("openrouter_api_key")
                      or (lokal.get("custom_llm") or {}).get("api_key") or "")
            eintrag = {k: str(neu.get(k) or "").strip() for k in localconfig.PROVIDER_FIELDS
                       if k != "api_key"}
            eintrag["api_key"] = (str(neu["api_key"] or "").strip() if "api_key" in neu
                                  else bisher)
            patch["openai_compat"] = {k: v for k, v in eintrag.items() if v} or None
            # Die alten Felder gehen im neuen Anbieter auf.
            patch["openrouter_api_key"] = None
            patch["custom_llm"] = None
        else:
            raise HTTPException(400, "openai_compat muss ein Objekt sein")
    localconfig.update(patch)
    return localconfig.redacted()


@app.get("/api/health")
async def get_health(user: str = Depends(current_user)) -> dict:
    """Erreichbarkeit der LLM-Endpunkte - bewusst getrennt von /api/config.

    Der Health-Check wartet je Endpunkt bis zu 4 s auf eine Verbindung. Lag er
    in /api/config, war die Oberfläche so lange nicht bedienbar, und ein Klick
    aufs Zahnrad in dieser Zeit lief ins Leere.
    """
    endpunkte = _llm_endpoints(user)
    return {"endpoints": await ChatClient(endpunkte).health() if endpunkte else []}


def _konfig() -> Settings:
    """Die geltende Konfiguration. Am eigenen Rechner bringt der Nutzer seine
    Schlüssel selbst mit - im Bündel steckt keiner -, deshalb gelten dort die
    lokalen Einstellungen vor der .env."""
    if settings.hosted:
        return settings
    update = localconfig.settings_update(localconfig.load())
    return settings.model_copy(update=update) if update else settings


def _engines(user: str | None = None) -> list[Engine]:
    """Transkriptions-Dienste für diesen Benutzer.

    Gesperrte Einträge (`restricted`) erscheinen hinter dem Proxy nur, wenn
    sie in seiner Freigabeliste stehen. Die Prüfung sitzt hier und nicht in
    der Oberfläche: Wer die Kennung errät, soll den Dienst trotzdem nicht
    benutzen können."""
    return [e for e in _konfig().transcription_engines if _darf(e.id, e.restricted, user)]


def _summary_models(user: str | None = None) -> list[SummaryModel]:
    return [m for m in _konfig().summary_models if _darf(m.id, m.restricted, user)]


def _llm_endpoints(user: str | None = None) -> list[Endpoint]:
    return [e for e in _konfig().llm_endpoints if _darf(e.name, e.restricted, user)]


# -- Verbrauch je Benutzer ------------------------------------------------------


def _usage_dir():
    """Nur hinter dem Proxy, wo sich mehrere Benutzer die Schlüssel teilen.
    Der implizite Benutzer zahlt selbst und sieht es in seinen Konten."""
    return settings.usage_dir if settings.auth_proxy else None


def _llm_usage(user: str, kind: str):
    """Rückruf für ChatClient: jede Antwort als Zeile im Verbrauchsprotokoll."""
    def melde(endpoint: Endpoint, completion) -> None:
        usage.record(_usage_dir(), user, kind, endpoint.display,
                     prompt_tokens=completion.prompt_tokens,
                     completion_tokens=completion.completion_tokens,
                     cost=usage.cost_tokens(endpoint, completion.prompt_tokens,
                                            completion.completion_tokens))
    return melde


LIMIT_PREFIX = "lp-limit-"


def _limit(user: str) -> float | None:
    """Monatsgrenze dieses Benutzers in US-Dollar, None für unbegrenzt."""
    if not settings.auth_proxy or _is_admin(user):
        return None
    for gruppe in proxy_groups.get(user, set()):
        if gruppe.startswith(LIMIT_PREFIX):
            try:
                return max(0.0, float(gruppe[len(LIMIT_PREFIX):]))
            except ValueError:
                continue
    return settings.usage_limit_usd or None


def _kontingent(user: str) -> dict | None:
    grenze = _limit(user)
    if grenze is None:
        return None
    return {"limit": grenze, "used": round(usage.month_cost(_usage_dir(), user), 4)}


def _pruefe_kontingent(user: str) -> None:
    """Vor jedem bezahlten Aufruf. Eine laufende Aufnahme läuft zu Ende -
    gesperrt wird erst der nächste Schritt."""
    stand = _kontingent(user)
    if stand and stand["used"] >= stand["limit"]:
        raise HTTPException(429, f"Das Monatskontingent von ${stand['limit']:.2f} ist "
                                 "aufgebraucht. Ab dem nächsten Monat geht es weiter – "
                                 "oder frag den Betreiber.")


def _audio_usage(user: str, kind: str, engine: Engine, seconds: float) -> None:
    if seconds > 0:
        usage.record(_usage_dir(), user, kind, engine.name, seconds=seconds,
                     cost=usage.cost_audio(engine, seconds))


def _require(session_id: str, user: str) -> LiveSession:
    session = sessions.get(session_id)
    # Fremde Sitzungen sehen aus wie nicht vorhandene - kein Hinweis darauf,
    # dass die ID existiert.
    if session is None or owners.get(session_id) != user:
        raise HTTPException(404, "Sitzung unbekannt")
    session.touch()
    return session


# Dieselben Grenzen wie im Einstellungsdialog. Der Server prüft sie selbst:
# Jeder Protokolllauf kostet, und die Oberfläche ist keine Schranke.
TRIGGER_GRENZEN = {"trigger_seconds": (20.0, 600.0), "trigger_chars": (200, 8000)}


def _begrenzt(wert, unten, oben):
    try:
        zahl = type(unten)(wert)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Keine Zahl: {wert!r}") from None
    return min(max(zahl, unten), oben)


@app.post("/api/sessions")
async def create_session(payload: dict, user: str = Depends(current_user)) -> dict:
    output_type_id = payload.get("output_type", "meeting")
    output_type = app.state.output_types.get(output_type_id)
    if output_type is None:
        raise HTTPException(404, f"Unbekannter Ausgabetyp: {output_type_id}")
    strategy = payload.get("strategy") or output_type.default_strategy
    if strategy not in STRATEGIES:
        raise HTTPException(400, f"Unbekannte Strategie: {strategy}")

    _pruefe_kontingent(user)
    if settings.hosted and settings.auth_proxy:
        offen = [sid for sid, owner in owners.items()
                 if owner == user and sid in sessions and sessions[sid].running]
        if len(offen) >= settings.max_sessions_per_user:
            raise HTTPException(
                429,
                f"Höchstens {settings.max_sessions_per_user} gleichzeitige "
                f"Aufzeichnungen. Beende zuerst eine laufende.",
            )

    verfügbar = _engines(user)
    engines = {e.id: e for e in verfügbar}
    engine = engines.get(
        payload.get("engine") or default_engine(verfügbar, settings.hosted))
    if engine is None:
        raise HTTPException(400, f"Unbekannte Engine: {payload.get('engine')}")
    if not engine.ready:
        raise HTTPException(400, f"Für {engine.name} fehlt der API-Schlüssel")

    # LLM-Auswahl: "auto" behält die Prioritätsliste mit Failover, ein Name
    # bindet die Sitzung fest an diesen Endpunkt.
    wanted = payload.get("llm_endpoint") or "auto"
    verfuegbare_llm = _llm_endpoints(user)
    if wanted == "auto":
        if not verfuegbare_llm:
            raise HTTPException(400, "Kein Modell fürs Live-Protokoll verfügbar")
        chat = ChatClient(verfuegbare_llm, on_usage=_llm_usage(user, "live"))
    else:
        chosen = next((e for e in verfuegbare_llm if e.name == wanted), None)
        if chosen is None:
            raise HTTPException(400, f"Unbekannter LLM-Endpunkt: {wanted}")
        if not chosen.ready:
            raise HTTPException(400, f"Für {chosen.display} fehlt der API-Schlüssel")
        chat = ChatClient([chosen], on_usage=_llm_usage(user, "live"))

    # Gehostet gilt nur die eigene Liste des Nutzers (siehe Settings.vocabulary).
    vocabulary = str(payload.get("vocabulary")
                     or ("" if settings.hosted else settings.vocabulary))
    # Segmentgrenzen bewusst nicht: sie folgen aus Whispers
    # 30-Sekunden-Fenster, das ist keine Geschmacksfrage.
    overrides = {
        key: _begrenzt(payload[key], *TRIGGER_GRENZEN[key])
        for key in TRIGGER_GRENZEN
        if payload.get(key) is not None
    }
    session_settings = settings.model_copy(update=overrides) if overrides else settings

    session = LiveSession(
        output_type=output_type,
        strategy_name=strategy,
        transcription=create_from_engine(engine, vocabulary),
        chat=chat,
        settings=session_settings,
        # Vorgabe aus: Entscheidend ist die Zusammenfassung am Ende,
        # die Live-Fassung ist Zugabe und kostet je Lauf.
        live_protocol=payload.get("live_protocol") is True,
    )
    session.engine_id = engine.id
    if isinstance(payload.get("context"), dict):
        session.set_context(payload["context"])
    session.on_finish = lambda s: _audio_usage(user, "aufnahme", engine, s.audio_seconds)
    sessions[session.id] = session
    owners[session.id] = user
    logger.info("Sitzung %s angelegt (%s / %s / %s / LLM %s)",
                session.id, output_type.id, strategy, engine.id, wanted)
    return {"id": session.id, "output_type": output_type.id, "strategy": strategy,
            "engine": engine.id, "engine_local": engine.local,
            "llm_endpoint": wanted, "live_protocol": session.live_protocol}


@app.post("/api/transcribe")
async def transcribe_file(request: Request, engine: str = "", diarize: bool = False,
                          user: str = Depends(current_user)) -> dict:
    """Eine fertige Aufnahme am Stück transkribieren.

    Die Datei kommt als roher Body, nicht als Formular: Starlette legt
    Formular-Uploads ab 1 MB als temporäre Datei auf die Platte, und genau
    das darf gehostet nicht passieren. Sie geht unverändert an den
    Dienst - kein Dekodieren, keine Taktung. Zurück kommt das Transkript als
    Absätze mit Zeitmarke; gespeichert wird nichts."""
    _pruefe_kontingent(user)
    grenze = settings.max_upload_mb * 1024 * 1024
    angegeben = request.headers.get("content-length")
    if angegeben and angegeben.isdigit() and int(angegeben) > grenze:
        raise HTTPException(413, f"Die Datei ist größer als {settings.max_upload_mb} MB.")

    dienste = {e.id: e for e in _engines(user)}
    if diarize:
        # Sprecher unterscheiden kann nicht jeder Dienst. Der gewählte zählt
        # dann nicht: Es geht an den ersten, der es kann.
        dienst = next((e for e in dienste.values() if e.diarize and e.ready), None)
        if dienst is None:
            raise HTTPException(400, "Kein Dienst eingerichtet, der Sprecher unterscheidet.")
    else:
        dienst = dienste.get(engine)
    if dienst is None or dienst.streaming or dienst.type == "whisper-cpp" \
            or not dienst.ready:
        raise HTTPException(400, "Dieser Dienst nimmt keine ganze Datei an.")

    daten = bytearray()
    async for stueck in request.stream():
        daten.extend(stueck)
        if len(daten) > grenze:  # falls Content-Length fehlte oder falsch war
            raise HTTPException(413, f"Die Datei ist größer als {settings.max_upload_mb} MB.")
    if not daten:
        raise HTTPException(400, "Keine Audiodaten")

    art = request.headers.get("content-type") or "application/octet-stream"
    # Header tragen nur Latin-1; der Browser schickt den Namen deshalb kodiert.
    name = unquote(request.headers.get("x-filename") or "") or "aufnahme"
    beginn = time.monotonic()
    try:
        segmente = await create_from_engine(dienst, settings.vocabulary).transcribe_file(
            bytes(daten), name, art, diarize=diarize)
    except TranscriptionError as exc:
        raise HTTPException(502, f"Transkription fehlgeschlagen: {exc}") from exc
    finally:
        daten.clear()
    dauer = round(time.monotonic() - beginn, 1)
    logger.info("Datei mit %s transkribiert: %d Segmente in %.1f s",
                dienst.id, len(segmente), dauer)
    _audio_usage(user, "sprecher" if diarize else "datei", dienst,
                 segmente[-1]["end"] if segmente else 0.0)
    return {"transcript": paragraphs(segmente), "engine": dienst.id,
            "seconds": dauer, "duration": segmente[-1]["end"] if segmente else 0.0}


# Obergrenze für ein Transkript, das zur Zusammenfassung geschickt wird. Drei
# Stunden sind rund 30.000 Wörter, also gut 200.000 Zeichen - das Doppelte
# lässt Luft und hält trotzdem niemanden davon ab, den Speicher zu fluten.
MAX_SUMMARY_CHARS = 400_000


@app.post("/api/summarize")
async def summarize(payload: dict, user: str = Depends(current_user)) -> dict:
    """Das ganze Transkript mit dem gewählten Modell zusammenfassen.

    Hängt an keiner Sitzung: Der Browser schickt Transkript, Terminrahmen und
    Optionen, zurück kommt Markdown. Gespeichert wird nichts."""
    _pruefe_kontingent(user)
    output_type = app.state.output_types.get(payload.get("output_type") or "meeting")
    if output_type is None:
        raise HTTPException(404, "Unbekannter Ausgabetyp")
    modelle = {m.id: m for m in _summary_models(user)}
    modell = modelle.get(payload.get("model") or "")
    if modell is None:
        raise HTTPException(400, "Unbekanntes oder nicht eingerichtetes Modell")

    eintraege = payload.get("transcript")
    if not isinstance(eintraege, list) or not eintraege:
        raise HTTPException(400, "Kein Transkript")
    eintraege = [e for e in eintraege if isinstance(e, dict)]
    if sum(len(str(e.get("text") or "")) for e in eintraege) > MAX_SUMMARY_CHARS:
        raise HTTPException(413, "Das Transkript ist für eine Zusammenfassung zu lang.")

    optionen = payload.get("options") if isinstance(payload.get("options"), dict) else {}
    detail = optionen.get("detail") if optionen.get("detail") in summary.DETAIL \
        else summary.DEFAULT_DETAIL
    bausteine = optionen.get("blocks")
    bausteine = [str(b) for b in bausteine] if isinstance(bausteine, list) else None
    feedback = payload.get("feedback")
    feedback = [str(f) for f in feedback] if isinstance(feedback, list) else []
    kontext = payload.get("context") if isinstance(payload.get("context"), dict) else {}

    try:
        ergebnis = await summary.summarize(
            ChatClient([modell.endpoint], on_usage=_llm_usage(user, "zusammenfassung")),
            output_type, eintraege,
            context=kontext, detail=detail, blocks=bausteine,
            freitext=str(optionen.get("freitext") or "")[:4000], feedback=feedback,
            personen=optionen.get("persons") is True,
            glossar=str(payload.get("glossary") or "")[:4000])
    except AllEndpointsFailed as exc:
        logger.error("Zusammenfassung fehlgeschlagen: %s", exc)
        raise HTTPException(502, f"Die Zusammenfassung ist fehlgeschlagen: {exc}") from exc
    if not ergebnis.markdown:
        raise HTTPException(502, "Das Modell hat keinen Text geliefert.")
    logger.info("Zusammenfassung mit %s: %d/%d Tokens, %.1f s", modell.id,
                ergebnis.prompt_tokens, ergebnis.completion_tokens, ergebnis.seconds)
    return {"markdown": ergebnis.markdown,
            "model": {"id": modell.id, "name": modell.name, "label": modell.label},
            "prompt_tokens": ergebnis.prompt_tokens,
            "completion_tokens": ergebnis.completion_tokens,
            "seconds": ergebnis.seconds}


@app.post("/api/revise")
async def revise(payload: dict, user: str = Depends(current_user)) -> dict:
    """Anmerkungen ins Protokoll einarbeiten. Zurück kommen nur
    Ersetzungen; eingesetzt werden sie im Browser."""
    _pruefe_kontingent(user)
    modelle = {m.id: m for m in _summary_models(user)}
    modell = modelle.get(payload.get("model") or "") or next(iter(modelle.values()), None)
    if modell is None:
        raise HTTPException(400, "Kein Modell eingerichtet")
    markdown = str(payload.get("markdown") or "")
    anmerkungen = [a for a in payload.get("annotations") or []
                   if isinstance(a, dict) and str(a.get("comment") or "").strip()]
    if not markdown.strip() or not anmerkungen:
        raise HTTPException(400, "Protokoll und mindestens eine Anmerkung nötig")
    if len(markdown) > MAX_SUMMARY_CHARS or len(anmerkungen) > 50:
        raise HTTPException(413, "Zu viel auf einmal")
    try:
        return await summary.revise(
            ChatClient([modell.endpoint], on_usage=_llm_usage(user, "anmerkungen")),
            markdown, anmerkungen)
    except AllEndpointsFailed as exc:
        raise HTTPException(502, f"Einarbeiten fehlgeschlagen: {exc}") from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/export/docx")
async def export_docx(payload: dict, user: str = Depends(current_user)) -> Response:
    """Markdown aus dem Browser als Word-Datei zurück. Gespeichert wird
    nichts - der Server wandelt nur um."""
    markdown = str(payload.get("markdown") or "")
    if not markdown.strip():
        raise HTTPException(400, "Nichts zu exportieren")
    if len(markdown) > MAX_SUMMARY_CHARS:
        raise HTTPException(413, "Der Text ist zu lang für den Export.")
    meta = [str(m) for m in payload.get("meta") or [] if str(m).strip()][:20]
    daten = markdown_to_docx(markdown, str(payload.get("title") or ""), meta)
    name = re.sub(r"[^A-Za-z0-9äöüÄÖÜß._-]+", "-", str(payload.get("filename") or "export"))
    return Response(daten, media_type="application/vnd.openxmlformats-officedocument."
                                      "wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''"
                                                    f"{quote(name.removesuffix('.docx'))}.docx"})


@app.post("/api/sessions/{session_id}/pause")
async def pause_session(session_id: str, user: str = Depends(current_user)) -> dict:
    session = _require(session_id, user)
    session.pause()
    return {"paused": True}


@app.post("/api/sessions/{session_id}/resume")
async def resume_session(session_id: str, user: str = Depends(current_user)) -> dict:
    session = _require(session_id, user)
    session.resume()
    return {"paused": False}


@app.post("/api/sessions/{session_id}/context")
async def set_context(session_id: str, payload: dict, user: str = Depends(current_user)) -> dict:
    """Terminrahmen setzen oder nachreichen - gilt ab dem nächsten Lauf."""
    session = _require(session_id, user)
    session.set_context(payload if isinstance(payload, dict) else {})
    return {"context": session.context}


@app.post("/api/sessions/{session_id}/gap")
async def note_gap(session_id: str, payload: dict, user: str = Depends(current_user)) -> dict:
    """Lücke im Mitschnitt melden, wenn der Browser die Aufnahme angehalten hat."""
    session = _require(session_id, user)
    try:
        seconds = int(payload.get("seconds") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds muss eine Zahl sein")
    session.note_gap(seconds, "diktat" if payload.get("reason") == "diktat" else "unterbrochen")
    return {"noted": True}


@app.post("/api/sessions/{session_id}/correct")
async def correct(session_id: str, payload: dict, user: str = Depends(current_user)) -> dict:
    """Korrektur aus dem Transkript: ersetzt im Server-Transkript,
    gilt für alles Weitere und bringt die Begriffsliste der Erkennung auf
    den neuen Stand."""
    session = _require(session_id, user)
    alt = str(payload.get("alt") or "")[:200]
    neu = str(payload.get("neu") or "")[:200]
    vocabulary = payload.get("vocabulary")
    anzahl = session.correct(alt, neu, str(vocabulary)[:4000] if vocabulary is not None else None)
    return {"replaced": anzahl, "vocabulary_live": not session.streaming}


@app.post("/api/sessions/{session_id}/feedback")
async def add_feedback(session_id: str, payload: dict, user: str = Depends(current_user)) -> dict:
    """Hinweis zur Protokollführung - geht nicht ins Transkript."""
    session = _require(session_id, user)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Leerer Hinweis")
    protocol = await session.add_feedback(text)
    return {"protocol": protocol, "feedback": session.feedback}


@app.post("/api/sessions/{session_id}/transcribe")
async def transcribe_once(session_id: str, request: Request, user: str = Depends(current_user)) -> dict:
    """Einzelnes WAV transkribieren - fürs Diktieren von Feedback.

    Läuft über dieselbe Engine wie die Sitzung, landet aber bewusst nicht
    im Sitzungstranskript.
    """
    session = _require(session_id, user)
    wav = await request.body()
    if not wav:
        raise HTTPException(400, "Keine Audiodaten")
    client = session.transcription
    dienst = next((e for e in _engines(user) if e.id == session.engine_id), None)
    if isinstance(client, StreamingTranscriptionClient):
        # Ein Strom-Dienst nimmt keine fertige Aufnahme an. Für das Diktat
        # springt dann ein dateifähiger Dienst ein, wie beim Upload.
        dienst = next((e for e in _engines(user) if not e.streaming and e.ready), None)
        if dienst is None:
            raise HTTPException(400, "Zum Diktieren fehlt ein Dienst, der eine "
                                     "fertige Aufnahme annimmt.")
        client = create_from_engine(dienst, settings.vocabulary)
    try:
        text = await client.transcribe(wav, "feedback.wav")
    except TranscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    if dienst is not None:
        _audio_usage(user, "diktat", dienst, max(0, len(wav) - 44) / 2 / 16000)
    return {"text": text}


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str, user: str = Depends(current_user)) -> PlainTextResponse:
    session = _require(session_id, user)
    filename = f"protokoll-{session.id}.md"
    return PlainTextResponse(
        session.export_markdown(),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Stopp über den WebSocket, wenn gleich die Zusammenfassung folgt. Muss zum
# POST passen: Was zuerst ankommt, startet den Abschluss.
STOP_OHNE_LIVE_LAUF = "stop-without-live-pass"


@app.post("/api/sessions/{session_id}/stop")
async def stop_session(session_id: str, live_pass: bool = True,
                       user: str = Depends(current_user)) -> dict:
    session = _require(session_id, user)
    await session.stop(live_pass=live_pass)
    return {"id": session.id, "protocol": session.strategy.protocol,
            "transcript": session.transcript}


@app.post("/api/sessions/{session_id}/live-pass")
async def catch_up_live_pass(session_id: str, user: str = Depends(current_user)) -> dict:
    session = _require(session_id, user)
    return {"protocol": await session.catch_up_live_pass()}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: str = Depends(current_user)) -> dict:
    session = _require(session_id, user)
    return {"id": session.id, "running": session.running, "paused": session.paused,
            "cycle": session.cycle, "transcript": session.transcript,
            "protocol": session.strategy.protocol, "feedback": session.feedback,
            "context": session.context}


@app.websocket("/api/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, session_id: str) -> None:
    """Binär hinein: PCM-Frames. Text hinaus: Ereignisse als JSON."""
    # Der Handshake trägt im Browser keine eigenen Header. Der vorgeschaltete
    # Proxy prüft ihn aber wie jede Anfrage und setzt die Kopfzeilen selbst.
    user: str | None = IMPLICIT_USER
    if settings.auth_proxy:
        user = _user_from_proxy(websocket.headers)
        if user is None:
            await websocket.close(code=4401)
            return

    session = sessions.get(session_id)
    if session is None or owners.get(session_id) != user:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    if session._stop_task is not None:
        # Zu spät zurückgemeldet: Die Sitzung ist schon beendet. Eigener Code,
        # damit der Browser aufhört, es weiter zu versuchen.
        await websocket.close(code=4410)
        return
    queue = session.subscribe()
    session.start()

    async def push_events() -> None:
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass

    pusher = asyncio.create_task(push_events())
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (data := message.get("bytes")) is not None:
                await session.add_audio(data)
            elif message.get("text") in ("stop", STOP_OHNE_LIVE_LAUF):
                await session.stop(live_pass=message["text"] == "stop")
                break
    except WebSocketDisconnect:
        pass
    finally:
        pusher.cancel()
        session.unsubscribe(queue)
        if session.running and session.listeners == 0:
            asyncio.create_task(stop_if_orphaned(session))


# -- Gespeicherte Sitzungen ------------------------------------------------


def _nur_gehostet() -> None:
    if not settings.hosted:
        raise HTTPException(404, "Nur im gehosteten Betrieb")


def _vault_fehler(exc: vault.VaultError) -> HTTPException:
    return HTTPException(exc.status, exc.detail)


@app.get("/api/vault")
async def vault_status(user: str = Depends(current_user)) -> dict:
    """Eingewickelter Schlüssel und die Liste - alles Chiffrat."""
    _nur_gehostet()
    return vault.status(settings.vault_dir, user, settings.vault_quota_mb)


@app.put("/api/vault/key")
async def vault_key(payload: dict, user: str = Depends(current_user)) -> dict:
    _nur_gehostet()
    try:
        return vault.set_key(settings.vault_dir, user, payload)
    except vault.VaultError as exc:
        raise _vault_fehler(exc) from exc


@app.delete("/api/vault")
async def vault_reset(user: str = Depends(current_user)) -> dict:
    _nur_gehostet()
    vault.reset(settings.vault_dir, user)
    logger.info("Tresor von %s geleert", user)
    return {"deleted": True}


@app.put("/api/vault/items/{item_id}")
async def vault_put(item_id: str, request: Request, user: str = Depends(current_user)) -> dict:
    """Roher Body wie beim Upload: Chiffrat, das der Server nicht lesen kann."""
    _nur_gehostet()
    grenze = (settings.max_upload_mb + 10) * 1024 * 1024
    daten = bytearray()
    async for stueck in request.stream():
        daten.extend(stueck)
        if len(daten) > grenze:
            raise HTTPException(413, f"Größer als {settings.max_upload_mb} MB.")
    if not daten:
        raise HTTPException(400, "Leer")
    try:
        return vault.put_item(settings.vault_dir, user, item_id, bytes(daten),
                              request.headers.get("x-meta") or "", settings.vault_quota_mb)
    except vault.VaultError as exc:
        raise _vault_fehler(exc) from exc


@app.get("/api/vault/items/{item_id}")
async def vault_get(item_id: str, user: str = Depends(current_user)) -> FileResponse:
    _nur_gehostet()
    try:
        pfad = vault.item_path(settings.vault_dir, user, item_id)
    except vault.VaultError as exc:
        raise _vault_fehler(exc) from exc
    return FileResponse(pfad, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@app.delete("/api/vault/items/{item_id}")
async def vault_delete(item_id: str, user: str = Depends(current_user)) -> dict:
    _nur_gehostet()
    try:
        vault.delete_item(settings.vault_dir, user, item_id)
    except vault.VaultError as exc:
        raise _vault_fehler(exc) from exc
    return {"deleted": item_id}


# -- Meine Daten (Art. 15, 17, 20 DSGVO) ------------------------------------------


@app.get("/api/me/export")
async def my_data(user: str = Depends(current_user)) -> Response:
    """Alles, was der Server über diesen Benutzer weiß, als eine JSON-Datei:
    Einstellungen und Glossare, Verbrauch, die Liste der gespeicherten
    Sitzungen. Deren Inhalt ist verschlüsselt und kommt über die App."""
    daten = {"user": user, "exported": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
             "settings": userprefs.load(settings.user_prefs_dir, user),
             "usage": usage.lines_for(settings.usage_dir, user) if settings.auth_proxy else [],
             "saved_sessions": [{k: v for k, v in i.items() if k != "meta"}
                                for i in vault.status(settings.vault_dir, user,
                                                      settings.vault_quota_mb)["items"]]
             if settings.hosted else []}
    return Response(json.dumps(daten, ensure_ascii=False, indent=2),
                    media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="meine-daten.json"'})


@app.delete("/api/me")
async def delete_my_data(user: str = Depends(current_user)) -> dict:
    """Einstellungen, Glossare und gespeicherte Sitzungen löschen. Der
    Verbrauch bleibt bis zum Ablauf seiner Frist von 13 Monaten - er dient
    der Kostenkontrolle der Instanz und enthält keine Inhalte."""
    userprefs.delete(settings.user_prefs_dir, user)
    if settings.hosted:
        vault.reset(settings.vault_dir, user)
    logger.info("Daten von %s auf eigenen Wunsch gelöscht", user)
    return {"deleted": True}


# -- Verbrauchsübersicht (nur Gruppe admins) ------------------------------------


@app.get("/api/usage")
async def get_usage(month: str = "", user: str = Depends(current_user)) -> dict:
    if not _is_admin(user):
        raise HTTPException(403, "Nur für Administratoren")
    monate = usage.months(settings.usage_dir)
    if not re.fullmatch(r"\d{4}-\d{2}", month or ""):
        month = monate[0] if monate else time.strftime("%Y-%m")
    return {**usage.summary(settings.usage_dir, month), "months": monate}


@app.get("/{seite}")
async def overlay_page(seite: str) -> FileResponse:
    """Eine zusätzliche Seite aus dem Overlay, etwa /impressum.

    Ohne Anmeldung wie die statischen Dateien auch: Rechtliche Angaben müssen
    für jeden erreichbar sein. Der Name kommt aus einer engen Zeichenmenge,
    ein `../` läuft damit ins Leere statt ins Dateisystem."""
    overlay = _overlay()
    if overlay is None or not SEITENNAME.fullmatch(seite) \
            or f"{seite}.html" in OVERLAY_SPECIAL:
        raise HTTPException(404, "Nicht gefunden")
    pfad = overlay / f"{seite}.html"
    if not pfad.is_file():
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(pfad, headers={"Cache-Control": "no-cache"})


class RevalidatingStaticFiles(StaticFiles):
    """Statische Dateien, die der Browser immer gegenprüft.

    Ohne Cache-Control cachen Browser heuristisch. Nach einem Update lief
    deshalb weiter die alte app.js - der Dienst war neu, die Oberfläche nicht,
    und der Fehler sah aus wie ein Serverproblem. Mit no-cache fragt der
    Browser jedes Mal nach und bekommt dank ETag meist ein billiges 304.

    Liegt ein Overlay vor, wird dort zuerst gesucht. Das Verzeichnis wird je
    Anfrage gelesen, nicht beim Start eingehängt - so greift es auch in Tests,
    die die Einstellung erst nachträglich setzen.
    """

    def lookup_path(self, path: str):
        overlay = _overlay()
        self.all_directories = ([overlay, STATIC_DIR] if overlay is not None
                                else [STATIC_DIR])
        return super().lookup_path(path)

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")
