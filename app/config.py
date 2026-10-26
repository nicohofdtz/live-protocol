"""Konfiguration: Endpunkte, Trigger, Aufnahme.

Endpunkte sind eine PRIORISIERTE LISTE, keine Einzelwerte: Ein Rechner im
eigenen Netz ist nicht immer eingeschaltet, der Fallback ist also kein
Sonderfall, sondern Normalbetrieb.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

SAMPLE_RATE = 16000  # Whisper-Nativrate; der Browser liefert bereits so.

# Wo die auslieferbaren Fassungen liegen und wo nach neuen gefragt wird.
REPOSITORY = "nicohofdtz/live-protocol"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"

# Dialekte, die über api.mistral.ai laufen und denselben Schlüssel brauchen.
MISTRAL_TYPES = ("mistral", "mistral-realtime")


# Grobe Auskunft, was der jeweilige Anbieter mit den Daten macht - keine
# Zusicherung. Steht hier und nicht in den Vorgabe-Einträgen, damit auch ein
# selbst geschriebener .env-Eintrag eine Angabe hat statt einer leeren Zeile.
# Stand 2026-09, Quelle sind die Bedingungen der Anbieter.
# "Kein Training" gilt nicht von selbst: Mistral trainiert mit API-Daten,
# solange im Konto nicht widersprochen ist (Admin > Privacy > "Anonymous
# improvement data"), auch mit Pay-as-you-go. Das muss jeder Kontoinhaber
# selbst abschalten - deshalb steht die Bedingung im Text.
MISTRAL_PRIVACY = ("Bei Mistral, Verarbeitung in der EU (Endpunkt api.eu.mistral.ai). "
                   "Kein Training, sofern im Mistral-Konto abgeschaltet. "
                   "Aufbewahrung bis zu 30 Tage zur Missbrauchserkennung.")
OPENROUTER_PRIVACY = ("Über OpenRouter an den jeweiligen Modellanbieter, Verarbeitung "
                      "meist in den USA - keine EU-Garantie. Die Anwendung fordert je "
                      "Anfrage Anbieter ohne Speicherung und ohne Training an; was "
                      "OpenRouter selbst protokolliert, regelt das OpenRouter-Konto.")
OPENAI_PRIVACY = ("Bei OpenAI, Verarbeitung in der Regel in den USA - keine "
                  "EU-Garantie. Es gelten die API-Bedingungen von OpenAI.")
EIGENES_NETZ_PRIVACY = "Bleibt auf dem angegebenen Rechner bzw. im eigenen Netz."
FREMD_PRIVACY = "Fremder Dienst - es gelten die Bedingungen des Anbieters."

# Ohne eigene Angabe fragt der lokale whisper-server hier. Die Desktop-Fassung
# setzt WHISPER_URL beim Start auf den Port, den sie ihm gegeben hat.
WHISPER_URL_LOCAL = "http://127.0.0.1:8081"

# Vorlagen für den OpenAI-kompatiblen Anbieter. Sie füllen nur Adresse und
# Modellnamen vor - alles bleibt überschreibbar, und wer einen anderen Dienst
# nimmt, wählt "custom". Modellnamen ändern sich bei den Anbietern; die
# Vorgaben hier sind Ausgangspunkte, keine Zusage.
PRESETS: dict[str, dict[str, Any]] = {
    "mistral": {
        "name": "Mistral",
        "base_url": "https://api.eu.mistral.ai",
        "live_model": "open-mistral-nemo",
        "summary_model": "zai-glm-5-3",
        "transcription_model": "voxtral-mini-latest",
        "privacy": MISTRAL_PRIVACY,
        "note": "",
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api",
        # Gemma 4 26B war im Vergleich (2026-09) das schnellste Live-Modell
        # (5-10 s je Lauf) bei brauchbarem Ergebnis.
        "live_model": "google/gemma-4-26b-a4b-it",
        # Dasselbe GLM 5.3 wie bei Mistral, hier über OpenRouter.
        "summary_model": "z-ai/glm-5.3",
        "transcription_model": "openai/whisper-large-v3-turbo",
        "privacy": OPENROUTER_PRIVACY,
        "note": "Anfragen gehen meist an Server in den USA.",
        # Je Anfrage nur Anbieter ohne Speicherung (zdr) und ohne Training.
        "extra": {"provider": {"zdr": True, "data_collection": "deny", "sort": "latency"}},
        # Listenpreise der Vorgabemodelle in US-Dollar (je Million Tokens bzw.
        # je Stunde Audio), für die Verbrauchsübersicht. Stand 2026-10.
        "prices": {"google/gemma-4-26b-a4b-it": (0.09, 0.30),
                   "z-ai/glm-5.3": (0.079, 3.2),
                   "openai/whisper-large-v3-turbo": 0.012},
        "costs": {"google/gemma-4-26b-a4b-it": "unter 1 ct je Stunde",
                  "z-ai/glm-5.3": "rund 2 ct je Stunde Termin",
                  "openai/whisper-large-v3-turbo": "~1 ct je Stunde, 10–30 s verzögert"},
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com",
        # Bewusst ohne Modellnamen: OpenAI ersetzt seine Modelle häufig, eine
        # Vorgabe hier wäre bald veraltet. Die Oberfläche bittet darum, sie
        # einzutragen.
        "live_model": "",
        "summary_model": "",
        "transcription_model": "",
        "privacy": OPENAI_PRIVACY,
        "note": "Anfragen gehen an Server in den USA. Modellnamen bitte aus der "
                "Modellübersicht von OpenAI eintragen.",
    },
    "ollama": {
        "name": "Ollama (lokal)",
        "base_url": "http://localhost:11434",
        "live_model": "qwen3.5:9b",
        "summary_model": "qwen3.5:9b",
        "transcription_model": "",
        "privacy": EIGENES_NETZ_PRIVACY,
        "note": "",
        # Denkmodelle wie Qwen3 verbrennen sonst die Zeit im reasoning-Feld.
        "disable_thinking": True,
    },
    "custom": {
        "name": "Eigener Endpunkt",
        "base_url": "",
        "live_model": "",
        "summary_model": "",
        "transcription_model": "",
        "privacy": FREMD_PRIVACY,
        "note": "",
    },
}


def normalize_base_url(url: str) -> str:
    """Basisadresse ohne abschließendes "/v1": Die Clients hängen
    "/v1/chat/completions" bzw. "/v1/audio/transcriptions" selbst an, viele
    Anbieter nennen ihre Adresse aber schon mit "/v1"."""
    url = (url or "").strip().rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def is_local_url(url: str) -> bool:
    """Ob eine Adresse auf diesen Rechner oder ins eigene Netz zeigt."""
    host = (urlparse(url).hostname or "").lower()
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        adresse = ipaddress.ip_address(host)
    except ValueError:
        return False
    return adresse.is_loopback or adresse.is_private


def detect_preset(base_url: str) -> str:
    """Vorlage zu einer Adresse, wenn keine angegeben ist - für den
    Datenschutztext und die Anbieterwahl bei OpenRouter."""
    host = (urlparse(normalize_base_url(base_url)).hostname or "").lower()
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if host.endswith("api.openai.com"):
        return "openai"
    if host.endswith("mistral.ai"):
        return "mistral"
    if is_local_url(base_url) and urlparse(base_url).port == 11434:
        return "ollama"
    return "custom"


class Provider(BaseModel):
    """Der OpenAI-kompatible Anbieter: eine Adresse, ein Schlüssel, Modelle
    fürs Live-Protokoll, die Zusammenfassung und optional die Transkription
    über /v1/audio/transcriptions."""

    preset: str = ""
    base_url: str = ""
    api_key: str | None = None
    live_model: str = ""
    summary_model: str = ""
    transcription_model: str = ""

    @property
    def vorlage(self) -> dict[str, Any]:
        name = self.preset if self.preset in PRESETS else detect_preset(self.base_url)
        return PRESETS[name]

    @property
    def url(self) -> str:
        return normalize_base_url(self.base_url)

    @property
    def local(self) -> bool:
        return is_local_url(self.url)

    @property
    def configured(self) -> bool:
        return bool(self.url and (self.live_model or self.summary_model
                                  or self.transcription_model))

    @property
    def privacy(self) -> str:
        return EIGENES_NETZ_PRIVACY if self.local else self.vorlage["privacy"]


class Engine(BaseModel):
    """Ein Transkriptions-Backend zur Auswahl im Einstellungsmenü."""

    id: str
    name: str
    type: Literal["whisper-cpp", "openai-compatible", "mistral",
                  "mistral-realtime"] = "whisper-cpp"
    url: str
    model: str
    api_key: str | None = None
    # Welcher konfigurierte Schlüssel gilt, wenn keiner im Eintrag steht.
    # Mistral-Dialekte nehmen ihren eigenen, alles andere muss es sagen.
    # "openrouter" ist der frühere Name von "openai" und gilt weiter.
    key_ref: Literal["mistral", "openai", "openrouter"] | None = None
    # Gesperrt, bis ein Benutzer ihn ausdrücklich freigeschaltet bekommt
    # (Gruppe lp-<id> beim Proxy). Gedacht für Dienste, die Geld kosten.
    restricted: bool = False
    # Listenpreis in US-Dollar je Stunde Audio, für die Verbrauchsübersicht.
    # Eine Schätzung, keine Abrechnung - maßgeblich ist das Konto beim Anbieter.
    price_hour: float = 0.0
    # Was mit dem Audio geschieht, in einem Satz. Grobe Auskunft für die
    # Auswahl, ausdrücklich keine Zusicherung - deshalb steht sie in der
    # Konfiguration und nicht im Code: Ändert ein Anbieter seine Bedingungen,
    # ist das eine Zeile in der .env und kein neues Programm.
    privacy: str = ""
    # Steuert den Datenschutz-Hinweis in der Oberfläche: bei Cloud-Engines
    # verlässt das Audio den Rechner.
    local: bool = True
    # Grobe Kostenangabe für die Auswahl, z. B. "~1 ct je Stunde". Wie der
    # Datenschutztext Konfiguration statt Code: Preise ändern sich.
    cost: str = ""
    # Nur für Strom-Dienste: wie lange der Dienst sammeln darf, bevor er Text
    # schickt. Kleiner heißt früher sichtbar, aber weniger Kontext je Wort.
    realtime_delay_ms: int = 500

    @property
    def privacy_text(self) -> str:
        if self.privacy:
            return self.privacy
        if self.local:
            return "Das Audio verlässt diesen Rechner nicht."
        if self.type in MISTRAL_TYPES:
            return MISTRAL_PRIVACY
        if self.key_ref == "openrouter" or "openrouter.ai" in self.url:
            return OPENROUTER_PRIVACY
        return FREMD_PRIVACY

    @property
    def ready(self) -> bool:
        """Benutzbar: mit Schlüssel, oder ohne, weil er im eigenen Netz läuft."""
        return bool(self.api_key) or self.local or self.type == "whisper-cpp"

    @property
    def streaming(self) -> bool:
        """Stehende Verbindung statt Segment-für-Segment."""
        return self.type == "mistral-realtime"

    @property
    def diarize(self) -> bool:
        """Ob der Dienst Sprecher unterscheidet - bisher nur Voxtral als Datei."""
        return self.type == "mistral"

    @property
    def supports_vocabulary(self) -> bool:
        """Ob eine Begriffsliste beim Dienst ankommt.

        Whisper nimmt sie als Prompt, Voxtral als `context_bias`. Nur Voxtral
        Realtime lehnt sie ab (Fehlercode 3051)."""
        return not self.streaming


class Endpoint(BaseModel):
    """Ein Backend. `type` unterscheidet den API-Dialekt, nicht den Ort."""

    name: str
    # Was die Oberfläche zeigt, etwa "Gemma 4 · schnell". Ohne Angabe der Name.
    label: str = ""
    type: Literal["openai-compatible", "mistral"] = "openai-compatible"
    url: str
    model: str
    api_key: str | None = None
    # Denkmodelle (Qwen3) kosten Latenz; abschaltbar pro Endpunkt.
    disable_thinking: bool = False
    # Steuert den Datenschutz-Hinweis in der Oberfläche.
    local: bool = True
    privacy: str = ""
    # Welcher konfigurierte Schlüssel gilt, wenn keiner im Eintrag steht.
    key_ref: Literal["mistral", "openai", "openrouter"] | None = None
    # Zusätzliche Felder für jede Anfrage an diesen Endpunkt, etwa
    # reasoning_effort oder bei OpenRouter die Anbieterwahl ohne Speicherung
    # und Training ({"provider": {"zdr": true, "data_collection": "deny"}}).
    extra: dict[str, Any] = {}
    # Wie bei Engine: nur mit der Gruppe lp-<name> sichtbar.
    restricted: bool = False
    # Listenpreise in US-Dollar je Million Tokens (Eingabe, Ausgabe).
    price_in: float = 0.0
    price_out: float = 0.0
    cost: str = ""

    @property
    def display(self) -> str:
        return self.label or self.name

    @property
    def privacy_text(self) -> str:
        if self.privacy:
            return self.privacy
        if self.local:
            return "Bleibt im eigenen Netz."
        if self.key_ref == "openrouter" or "openrouter.ai" in self.url:
            return OPENROUTER_PRIVACY
        return MISTRAL_PRIVACY if self.type == "mistral" else FREMD_PRIVACY

    @property
    def ready(self) -> bool:
        return bool(self.api_key) or self.local


class SummaryModel(BaseModel):
    """Ein Modell zur Wahl für die Zusammenfassung nach dem Termin.

    Mehr als ein Endpunkt: Name, Geschwindigkeit, Kosten und Datenschutz stehen
    im Auswahldialog, damit niemand nachschlagen muss, was er wählt.
    """

    id: str
    name: str
    label: str
    speed: str = ""
    cost: str = ""
    privacy: str = ""
    # Nur mit der Gruppe lp-<id> sichtbar.
    restricted: bool = False
    endpoint: Endpoint

    @property
    def privacy_text(self) -> str:
        return self.privacy or self.endpoint.privacy_text


def default_engine(engines: list[Engine], hosted: bool) -> str:
    return _default_engine([e for e in engines if e.ready] or engines, hosted)


def _default_engine(engines: list[Engine], hosted: bool) -> str:
    """Welcher Dienst vorausgewählt ist.

    Unterschiedlich je Betriebsart, und zwar mit Absicht: Auf dem eigenen
    Rechner gewinnt der erste lokale Dienst - das Audio bleibt dort, solange
    niemand ausdrücklich etwas anderes wählt. Auf dem Server gewinnt der
    erste Eintrag der Liste; ein whisper.cpp-Server steht dort nur, wenn er
    ausdrücklich eingerichtet ist (WHISPER_URL), und dann vorn.
    """
    if not engines:
        return ""
    if not hosted:
        lokal = next((e for e in engines if e.local), None)
        if lokal is not None:
            return lokal.id
    return engines[0].id


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Anbieter ----------------------------------------------------------
    # Zwei Schlüssel genügen: Mistral (empfohlen, EU) kann alles, auch die
    # Mistral-eigenen Funktionen (Voxtral Realtime, Sprecher, context_bias).
    # Ein OpenAI-kompatibler Anbieter kann alles außer diesen. Aus den beiden
    # entstehen die Auswahllisten unten, solange sie niemand ausdrücklich
    # als JSON vorgibt.
    mistral_api_key: str | None = Field(default=None)

    openai_compat_preset: str = Field(default="")
    openai_compat_base_url: str = Field(default="")
    openai_compat_api_key: str | None = Field(default=None)
    openai_compat_live_model: str = Field(default="")
    openai_compat_summary_model: str = Field(default="")
    openai_compat_transcription_model: str = Field(default="")

    # Veraltet: Aus der Zeit vor dem allgemeinen OpenAI-kompatiblen Anbieter.
    # Steht nur dieser Schlüssel in der .env, gilt er als OpenAI-kompatibler
    # Anbieter mit der Vorlage OpenRouter.
    openrouter_api_key: str | None = Field(default=None)

    # Adresse eines whisper.cpp-Servers (whisper-server, Pfad /inference). Am
    # eigenen Rechner ist ohne Angabe 127.0.0.1:8081 gemeint. Auf dem Server
    # erscheint die lokale Transkription NUR, wenn die Adresse gesetzt ist -
    # dort hat nicht jeder einen whisper-server mit genug Rechenleistung.
    # TRANSCRIPTION_URL ist der frühere Name.
    whisper_url: str | None = Field(
        default=None, validation_alias=AliasChoices("WHISPER_URL", "TRANSCRIPTION_URL"))

    # Auswählbare Dienste und Modelle als JSON - nur für Sonderfälle, etwa
    # gesperrte Einträge je Gruppe oder mehrere Anbieter nebeneinander. Leer
    # heißt: aus den Anbietern oben gebildet. Aufbau siehe .env.example.
    transcription_engines_raw: str = Field(default="", alias="TRANSCRIPTION_ENGINES")
    llm_endpoints_raw: str = Field(default="", alias="LLM_ENDPOINTS")
    summary_models_raw: str = Field(default="", alias="SUMMARY_MODELS")

    # Eigennamen und Fachbegriffe, die die Erkennung sonst verfremdet.
    # Nur für den Desktop-Betrieb: Gehostet
    # pflegt jeder Nutzer seine eigene Liste, eine Vorgabe für den Server
    # trüge dieselben Begriffe zu allen.
    vocabulary: str = Field(default="")

    # Fortschreibung: auslösen nach Zeit ODER nach neuen Zeichen.
    trigger_seconds: float = Field(default=90.0)
    trigger_chars: int = Field(default=1200)

    # Segmentierung der Aufnahme. KEINE Einstellung, sondern eine Eigenschaft
    # des Modells: Whisper verarbeitet immer ein 30-Sekunden-Fenster und fuellt
    # kuerzeres Audio auf. Ein Maximum ueber 29 s oeffnet ein zweites, fast
    # leeres Fenster; ein sehr kleines Minimum kostet ueberproportional.
    # Gemessen: 29-s-Segment 3,09 s, 31-s-Segment 4,87 s.
    segment_min_seconds: float = Field(default=10.0)
    segment_max_seconds: float = Field(default=29.0)

    # --- Betriebsmodus -------------------------------------------------
    # desktop: läuft auf dem eigenen Rechner, keine Anmeldung, lokale Modelle
    #          verfügbar, eigene API-Schlüssel.
    # hosted:  läuft auf einem Server (meist hinter einem Reverse Proxy mit
    #          TLS), Schlüssel aus der .env, Sitzungen nur im Arbeitsspeicher,
    #          dazu "Meine Sitzungen" und der Hinweis auf die lokale Fassung.
    deployment_mode: Literal["desktop", "hosted"] = Field(default="desktop")

    # Ohne weitere Angabe gibt es genau einen, impliziten Benutzer: keine
    # Anmeldung, keine Verbrauchsgrenze. Wer den Dienst mehreren Leuten
    # öffnet, stellt einen Proxy davor, der Remote-User setzt (etwa Authelia
    # oder oauth2-proxy): Benutzer und Gruppen kommen dann als Kopfzeilen
    # Remote-User/Remote-Groups, mit Freigaben, Monatsgrenze und
    # Verbrauchsübersicht. Nur einschalten, wenn der Port ausschließlich vom
    # Proxy erreichbar ist - sonst ließe sich die Kopfzeile fälschen.
    auth_proxy: bool = Field(default=False)

    # --- Grenzen (greifen nur im gehosteten Betrieb) --------------------
    # Die Schlüssel in der .env zahlen für alle Sitzungen: ohne Obergrenze
    # genügt ein vergessener offener Tab, deshalb gilt die Sitzungsdauer
    # immer. Gleichzeitige Sitzungen begrenzt sie nur hinter dem Proxy, wo
    # sich mehrere Benutzer die Schlüssel teilen - der implizite Benutzer
    # nimmt vielleicht am Telefon auf, während am Rechner noch eine läuft.
    max_session_minutes: int = Field(default=180)
    max_sessions_per_user: int = Field(default=2)

    # Obergrenze für eine hochgeladene Datei. Sie liegt beim Verarbeiten ganz
    # im Arbeitsspeicher, und ein kleiner Server hat davon wenig. Drei
    # Stunden Sprachmemo (AAC, ~64 kbit/s) sind rund 90 MB.
    max_upload_mb: int = Field(default=150)

    # --- Lebensdauer von Sitzungen (in beiden Betriebsarten) --------------
    # Eine beendete Sitzung bleibt so lange im Speicher, bis sie so lange
    # niemand mehr angefasst hat - Zeit genug zum Exportieren. Danach fliegt
    # sie heraus: im gehosteten Betrieb sind es fremde Transkripte, und "nur
    # im Arbeitsspeicher" ist als Schutz gemeint, nicht als Dauerzustand.
    session_ttl_minutes: float = Field(default=30.0)
    # Reißt die Verbindung ohne Stopp ab (Tab zu, Netz weg), wartet die
    # Sitzung so lange auf eine neue Verbindung und beendet sich dann selbst.
    orphan_grace_seconds: float = Field(default=120.0)

    # Wohin der Hinweis auf die lokale Fassung und der Link in der Fußzeile
    # führen. Die Bündel liegen als Anhang an den GitHub-Releases.
    download_url: str = Field(default=RELEASES_URL)

    # --- Update-Prüfung (nur Desktop) -----------------------------------
    # Gefragt wird die GitHub-API nach dem neuesten Release dieses
    # Repositorys - dort liegen auch die Bündel, eine eigene Gegenstelle
    # braucht es nicht. Die Abfrage läuft im Backend, nicht im Browser (das
    # erspart CORS), und bleibt folgenlos, wenn nichts erreichbar ist.
    # UPDATE_CHECK=false schaltet sie ab, UPDATE_URL zeigt auf eine andere
    # Quelle: entweder dieselbe Form wie die GitHub-API (tag_name, html_url)
    # oder schlicht {"version": ..., "download_url": ...}.
    update_check: bool = Field(default=True)
    update_url: str = Field(default=LATEST_RELEASE_API)

    # --- Overlay ------------------------------------------------------------
    # Verzeichnis mit eigenen Dateien für eine Instanz, siehe docs/overlay.md.
    # Was dort liegt, wird zusätzlich zu oder anstelle von static/
    # ausgeliefert; weitere HTML-Seiten (etwa impressum.html) werden unter
    # /<name> erreichbar und bekommen einen Link in der Fußzeile.
    overlay_dir: Path | None = Field(default=None)
    # Hinweisleiste oben, als Klartext. Eine banner.html im Overlay geht vor.
    banner_text: str = Field(default="")
    # Vor jeder Aufnahme fragen, ob alle Anwesenden einverstanden sind.
    # Abschaltbar für Instanzen, die das anders regeln.
    consent_hint: bool = Field(default=True)

    # Einmaliger Abschlusslauf nach dem Stoppen (glättet die Delta-Struktur).
    final_pass: bool = Field(default=True)

    # Sitzungen werden lokal auf Platte geschrieben (Absturzsicherheit).
    sessions_dir: Path = Field(default=Path.home() / ".local/share/live-protocol/sessions")
    user_prefs_dir: Path = Field(
        default=Path.home() / ".local/share/live-protocol/prefs",
        description="Einstellungen je Benutzer. Keine Sitzungsinhalte - die "
                    "bleiben im gehosteten Betrieb im Arbeitsspeicher.")
    # Verbrauch je Benutzer (nur mit AUTH_PROXY), eine Datei je Monat. Siehe
    # app/usage.py - keine Inhalte, nur Mengen.
    usage_dir: Path = Field(default=Path.home() / ".local/share/live-protocol/usage")
    # Gespeicherte Sitzungen (nur gehostet): im Browser verschlüsselt, der
    # Server sieht nur Chiffrat. Siehe app/vault.py.
    vault_dir: Path = Field(default=Path.home() / ".local/share/live-protocol/vault")
    vault_quota_mb: int = Field(default=1000)
    # Monatliche Obergrenze je Benutzer in US-Dollar (geschätzt, siehe
    # app/usage.py), nur mit AUTH_PROXY. 0 heißt aus. Die Gruppe lp-limit-<zahl> setzt für einen
    # Benutzer eine eigene Grenze, die Gruppe admins hat keine.
    usage_limit_usd: float = Field(default=0.0)

    @property
    def hosted(self) -> bool:
        return self.deployment_mode == "hosted"

    @property
    def provider(self) -> Provider:
        """Der OpenAI-kompatible Anbieter, mit Vorlage aufgefüllt.

        Leere Felder füllt die Vorlage. Steht nur der alte
        OPENROUTER_API_KEY in der .env, wird daraus die Vorlage OpenRouter."""
        preset = self.openai_compat_preset
        schluessel = self.openai_compat_api_key
        if not schluessel and not self.openai_compat_base_url and self.openrouter_api_key:
            preset, schluessel = preset or "openrouter", self.openrouter_api_key
        if not preset:
            preset = detect_preset(self.openai_compat_base_url) \
                if self.openai_compat_base_url else ""
        vorlage = PRESETS.get(preset, PRESETS["custom"])
        if not (self.openai_compat_base_url or schluessel or self.openai_compat_preset):
            # Nichts eingerichtet - dann auch keine Vorlage vorfüllen.
            return Provider()
        return Provider(
            preset=preset,
            base_url=self.openai_compat_base_url or vorlage["base_url"],
            api_key=schluessel or None,
            live_model=self.openai_compat_live_model or vorlage["live_model"],
            summary_model=self.openai_compat_summary_model or vorlage["summary_model"],
            transcription_model=(self.openai_compat_transcription_model
                                 or vorlage["transcription_model"]),
        )

    @property
    def whisper_offered(self) -> bool:
        """Ob die lokale Transkription in der Auswahl steht: am eigenen
        Rechner immer, auf dem Server nur mit ausdrücklicher Adresse."""
        return not self.hosted or bool(self.whisper_url)

    def key_for(self, engine: Engine | Endpoint) -> str | None:
        """Der Schlüssel, den ein Eintrag ohne eigenen benutzt."""
        ref = engine.key_ref or ("mistral" if engine.type in MISTRAL_TYPES else None)
        if ref == "mistral":
            return self.mistral_api_key
        if ref in ("openai", "openrouter"):
            return self.provider.api_key
        return None

    def _vorgabe_engines(self) -> list[dict]:
        """Transkription aus den Anbietern. Reihenfolge = Vorrang auf dem
        Server: der eigene whisper-server, dann der OpenAI-kompatible Dienst
        (meist deutlich billiger, dafür 10-30 s verzögert - das
        Live-Transkript ist nur Signal und Rückfallebene, maßgeblich ist die
        Zusammenfassung am Ende), dann Voxtral."""
        engines: list[dict] = []
        if self.whisper_offered:
            engines.append({
                "id": "whisper-local",
                "name": "Whisper (lokal)" if not self.hosted else "Whisper (whisper.cpp-Server)",
                "type": "whisper-cpp", "url": self.whisper_url or WHISPER_URL_LOCAL,
                "model": "whisper-large-v3-turbo", "local": True,
                **({"privacy": "Das Audio geht an den eingerichteten whisper.cpp-Server, "
                               "nicht an Dritte."} if self.hosted else {})})
        anbieter = self.provider
        if anbieter.configured and anbieter.transcription_model:
            vorlage = anbieter.vorlage
            modell = anbieter.transcription_model
            engines.append({
                "id": "openai-compatible",
                "name": f"{modell} ({vorlage['name']}"
                        + (")" if anbieter.local else ", Cloud)"),
                "type": "openai-compatible", "url": anbieter.url, "model": modell,
                "local": anbieter.local, "key_ref": "openai", "privacy": anbieter.privacy,
                "cost": vorlage.get("costs", {}).get(modell, ""),
                "price_hour": vorlage.get("prices", {}).get(modell, 0.0)})
        # Mistral-Einträge am eigenen Rechner auch ohne Schlüssel: Er lässt sich
        # in den Einstellungen nachtragen, bis dahin steht der Eintrag gesperrt.
        # Alle Mistral-Dienste über den EU-Endpunkt: api.mistral.ai sagt keinen
        # Verarbeitungsort zu. Kostet das 1,1-Fache, gemessen 2026-09-23 laufen
        # Datei und Realtime dort gleich.
        if self.mistral_api_key or not self.hosted:
            engines += [
                {"id": "voxtral-realtime", "name": "Voxtral Realtime (Mistral, Cloud)",
                 "type": "mistral-realtime", "url": "https://api.eu.mistral.ai",
                 "model": "voxtral-mini-transcribe-realtime-2602", "local": False,
                 "cost": "~33 ct je Stunde, sofort", "price_hour": 0.396},
                {"id": "voxtral", "name": "Voxtral (Mistral, Cloud)", "type": "mistral",
                 "url": "https://api.eu.mistral.ai", "model": "voxtral-mini-latest",
                 "local": False, "cost": "~17 ct je Stunde, 10–30 s verzögert",
                 "price_hour": 0.198},
            ]
        return engines

    def _vorgabe_llm(self) -> list[dict]:
        """Live-Protokoll aus den Anbietern: erst der OpenAI-kompatible
        (schneller bzw. im eigenen Netz), dann Mistral Nemo als Ausweichweg."""
        endpunkte: list[dict] = []
        anbieter = self.provider
        if anbieter.configured and anbieter.live_model:
            vorlage = anbieter.vorlage
            modell = anbieter.live_model
            preis = vorlage.get("prices", {}).get(modell, (0.0, 0.0))
            endpunkte.append({
                "name": "openai-compatible", "label": f"{modell} · {vorlage['name']}",
                "type": "openai-compatible", "url": anbieter.url, "model": modell,
                "local": anbieter.local, "key_ref": "openai", "privacy": anbieter.privacy,
                "disable_thinking": vorlage.get("disable_thinking", False),
                "extra": vorlage.get("extra", {}),
                "price_in": preis[0], "price_out": preis[1],
                "cost": vorlage.get("costs", {}).get(modell, "")})
        # Mistral bleibt am eigenen Rechner auch ohne Schlüssel stehen: Den
        # trägt die Desktop-Fassung erst später in den Einstellungen nach.
        if self.mistral_api_key or not self.hosted:
            endpunkte.append({
                "name": "mistral", "label": "Mistral Nemo · EU", "type": "mistral",
                "url": "https://api.eu.mistral.ai", "model": "open-mistral-nemo",
                "local": False, "price_in": 0.165, "price_out": 0.165,
                "cost": "rund 1 ct je Stunde"})
        return endpunkte

    def _vorgabe_summary(self) -> list[dict]:
        """Zusammenfassung aus den Anbietern.

        Bei Mistral zweimal GLM 5.3 in der EU mit zwei Denkstufen, Gründlich
        vorn: Anlass des Umbaus war die Qualität. GLM 5.3 Flash über
        OpenRouter ist geflogen: Mit ZDR war es langsamer als diese beiden,
        ohne EU-Garantie, und der Preisvorteil lag unter 2 ct je Lauf."""
        modelle: list[dict] = []
        if self.mistral_api_key or not self.hosted:
            modelle += [
                {"id": "eu", "name": "GLM 5.3", "label": "Gründlich & EU",
                 "speed": "15–30 s", "cost": "rund 3 ct je Stunde Termin",
                 "endpoint": {"name": "glm-5.3 (Mistral EU)", "type": "mistral",
                              "url": "https://api.eu.mistral.ai", "model": "zai-glm-5-3",
                              "local": False, "key_ref": "mistral",
                              "extra": {"reasoning_effort": "high"},
                              "price_in": 1.54, "price_out": 4.84}},
                {"id": "eu-schnell", "name": "GLM 5.3", "label": "Schnell & EU",
                 "speed": "5–15 s", "cost": "rund 2 ct je Stunde Termin",
                 "endpoint": {"name": "glm-5.3 schnell (Mistral EU)", "type": "mistral",
                              "url": "https://api.eu.mistral.ai", "model": "zai-glm-5-3",
                              "local": False, "key_ref": "mistral",
                              "extra": {"reasoning_effort": "low"},
                              "price_in": 1.54, "price_out": 4.84}},
            ]
        anbieter = self.provider
        if anbieter.configured and anbieter.summary_model:
            vorlage = anbieter.vorlage
            modell = anbieter.summary_model
            preis = vorlage.get("prices", {}).get(modell, (0.0, 0.0))
            modelle.append({
                "id": "openai-compatible", "name": modell, "label": vorlage["name"],
                "privacy": anbieter.privacy,
                "cost": vorlage.get("costs", {}).get(modell, ""),
                "endpoint": {"name": f"{modell} ({vorlage['name']})",
                             "type": "openai-compatible", "url": anbieter.url,
                             "model": modell, "local": anbieter.local, "key_ref": "openai",
                             "disable_thinking": vorlage.get("disable_thinking", False),
                             "extra": vorlage.get("extra", {}),
                             "price_in": preis[0], "price_out": preis[1]}})
        return modelle

    @staticmethod
    def _liste(raw: str, vorgabe) -> list[dict]:
        return json.loads(raw) if raw.strip() else vorgabe()

    @property
    def transcription_engines(self) -> list[Engine]:
        engines = [Engine(**item) for item in
                   self._liste(self.transcription_engines_raw, self._vorgabe_engines)]
        for engine in engines:
            if engine.api_key is None:
                engine.api_key = self.key_for(engine)
        return engines

    @property
    def summary_models(self) -> list[SummaryModel]:
        """Nur Modelle, die benutzbar sind - ein Eintrag, der beim Klick
        scheitert, gehört nicht in die Auswahl."""
        modelle = [SummaryModel(**item) for item in
                   self._liste(self.summary_models_raw, self._vorgabe_summary)]
        for modell in modelle:
            if modell.endpoint.api_key is None:
                modell.endpoint.api_key = self.key_for(modell.endpoint)
        return [m for m in modelle if m.endpoint.ready]

    @property
    def llm_endpoints(self) -> list[Endpoint]:
        endpoints = [Endpoint(**item) for item in
                     self._liste(self.llm_endpoints_raw, self._vorgabe_llm)]
        for endpoint in endpoints:
            if endpoint.api_key is None:
                endpoint.api_key = self.key_for(endpoint)
        # Ein Cloud-Eintrag ohne Schlüssel kostet bei "Automatisch" nur einen
        # vergeblichen Aufruf je Lauf. Mistral bleibt stehen: Dessen Schlüssel
        # trägt die Desktop-Fassung erst später in den Einstellungen nach.
        return [e for e in endpoints if e.ready or e.type == "mistral"]


settings = Settings()
