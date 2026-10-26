"""API: der implizite Benutzer, Fremdzugriff hinter dem Proxy, Grenzen."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, owners, sessions


def _ohne_anbieter(monkeypatch):
    """Nicht aus einer lokalen .env erben - die Tests sollen überall gleich laufen."""
    for feld in ("openrouter_api_key", "openai_compat_api_key", "whisper_url"):
        monkeypatch.setattr(settings, feld, None)
    for feld in ("openai_compat_preset", "openai_compat_base_url", "openai_compat_live_model",
                 "openai_compat_summary_model", "openai_compat_transcription_model",
                 "transcription_engines_raw", "llm_endpoints_raw", "summary_models_raw"):
        monkeypatch.setattr(settings, feld, "")
    monkeypatch.setattr(settings, "auth_proxy", False)


@pytest.fixture
def hosted(monkeypatch, tmp_path):
    """Server-Betrieb ohne Proxy: ein impliziter Benutzer, nur ein Mistral-Schlüssel."""
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    monkeypatch.setattr(settings, "vault_dir", tmp_path / "vault")
    monkeypatch.setattr(settings, "usage_dir", tmp_path / "usage")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    sessions.clear()
    owners.clear()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    from app import localconfig
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "desktop")
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    monkeypatch.setattr(settings, "mistral_api_key", None)
    monkeypatch.setattr(localconfig, "config_file", lambda: tmp_path / "config.json")
    sessions.clear()
    owners.clear()
    with TestClient(app) as client:
        yield client


def test_server_ohne_proxy_braucht_keine_anmeldung(hosted):
    """Ein Benutzer, keine Anmeldung - es genügt eine .env mit Schlüssel."""
    antwort = hosted.get("/api/config")
    assert antwort.status_code == 200
    config = antwort.json()
    assert config["user"] == "local"
    assert config["quota"] is None and config["admin"] is False
    assert "www-authenticate" not in antwort.headers
    assert hosted.get("/").status_code == 200


def test_server_ohne_proxy_ohne_verbrauchsgrenze(hosted, monkeypatch):
    """Die Monatsgrenze gilt nur hinter dem Proxy, wo sich mehrere Leute die
    Schlüssel teilen. Erfasst wird für den impliziten Benutzer auch nichts."""
    monkeypatch.setattr(settings, "usage_limit_usd", 0.01)
    assert hosted.get("/api/config").json()["quota"] is None
    assert hosted.get("/api/usage").status_code == 403
    assert not (settings.usage_dir).exists()


def test_meine_sitzungen_fuer_den_impliziten_benutzer(hosted):
    schluessel = {"salt": "c2FsdA==", "wrapped": "d3JhcHBlZA==", "iterations": 600_000}
    assert hosted.get("/api/config").json()["vault"] is True
    assert hosted.put("/api/vault/key", json=schluessel).status_code == 200
    kennung = "0123456789abcdef"
    assert hosted.put(f"/api/vault/items/{kennung}", content=b"chiffrat",
                      headers={"X-Meta": "bWV0YQ=="}).status_code == 200
    assert [i["id"] for i in hosted.get("/api/vault").json()["items"]] == [kennung]
    assert hosted.get(f"/api/vault/items/{kennung}").content == b"chiffrat"


def test_websocket_ohne_proxy_offen(hosted):
    sid = hosted.post("/api/sessions", json={"engine": "voxtral"}).json()["id"]
    sessions[sid]._stop_task = object()   # gleich wieder schließen
    with hosted.websocket_connect(f"/api/sessions/{sid}/stream"):
        pass


def test_gehostet_bietet_nur_eingerichtete_dienste(hosted):
    config = hosted.get("/api/config").json()
    # Ohne WHISPER_URL kein lokales Whisper; mit nur einem Mistral-Schlüssel
    # bleiben die Voxtral-Dienste, vorausgewählt Voxtral Realtime.
    assert [e["id"] for e in config["engines"]] == ["voxtral-realtime", "voxtral"]
    assert config["defaults"]["engine"] == "voxtral-realtime"
    assert [e["name"] for e in config["llm_endpoints"]] == ["mistral"]
    assert [m["id"] for m in config["summary_models"]] == ["eu", "eu-schnell"]
    assert config["mode"] == "hosted"
    assert config["download_url"]


def test_whisper_server_auf_dem_server_nur_ausdruecklich(hosted, monkeypatch):
    """Lokale Dienste stehen auf dem Server, wenn sie eingerichtet sind -
    dann auch vorn, weil sich jemand bewusst dafür entschieden hat."""
    monkeypatch.setattr(settings, "whisper_url", "http://whisper:8081")
    config = hosted.get("/api/config").json()
    whisper = config["engines"][0]
    assert whisper["id"] == "whisper-local" and whisper["local"] is True
    assert config["defaults"]["engine"] == "whisper-local"
    assert "nicht an Dritte" in whisper["privacy"]
    from app.main import _engines
    assert _engines()[0].url == "http://whisper:8081"
    assert hosted.post("/api/sessions", json={"engine": "whisper-local"}).status_code == 200


def test_impliziter_benutzer_ohne_grenze_gleichzeitiger_sitzungen(hosted, monkeypatch):
    """Am Telefon aufnehmen, während am Rechner noch eine Sitzung läuft -
    für den einen Benutzer ohne Proxy gibt es keine Höchstzahl. Die Dauer
    einer Sitzung bleibt begrenzt."""
    monkeypatch.setattr(settings, "max_sessions_per_user", 1)
    for _ in range(3):
        antwort = hosted.post("/api/sessions", json={"output_type": "meeting"})
        assert antwort.status_code == 200
        sessions[antwort.json()["id"]].running = True
    grenzen = hosted.get("/api/config").json()["limits"]
    assert grenzen == {"session_minutes": settings.max_session_minutes,
                       "sessions_per_user": None}


def test_desktop_braucht_keine_anmeldung(desktop):
    config = desktop.get("/api/config").json()
    assert config["mode"] == "desktop"
    assert config["download_url"] is None
    assert config["defaults"]["engine"] == "whisper-local"
    # Mistral steht auch ohne Schlüssel da, gesperrt, bis er eingetragen ist.
    voxtral = next(e for e in config["engines"] if e["id"] == "voxtral")
    assert voxtral["configured"] is False


def test_desktop_whisper_folgt_whisper_url(desktop, monkeypatch):
    """Die Desktop-Fassung gibt dem whisper-server einen freien Port und sagt
    ihn über WHISPER_URL - die Engine muss dorthin zeigen, nicht auf 8081."""
    from app.main import _engines
    assert _engines()[0].url == "http://127.0.0.1:8081"
    monkeypatch.setattr(settings, "whisper_url", "http://127.0.0.1:50123")
    assert _engines()[0].url == "http://127.0.0.1:50123"


def test_segmentgrenzen_sind_keine_einstellung(hosted, monkeypatch):
    """Sie folgen aus Whispers 30-Sekunden-Fenster und duerfen nicht von
    aussen verstellt werden."""
    monkeypatch.setattr(settings, "segment_min_seconds", 10.0)
    monkeypatch.setattr(settings, "segment_max_seconds", 29.0)

    antwort = hosted.post("/api/sessions",
                          json={"output_type": "meeting",
                                "segment_min_seconds": 1, "segment_max_seconds": 300})
    assert antwort.status_code == 200
    sitzung = sessions[antwort.json()["id"]]
    assert sitzung.settings.segment_min_seconds == 10.0
    assert sitzung.settings.segment_max_seconds == 29.0

    config = hosted.get("/api/config").json()
    assert "segment_min_seconds" not in config["defaults"]


def test_statische_dateien_werden_immer_gegengeprueft(desktop):
    """Ohne no-cache liefe nach einem Deploy weiter die alte app.js."""
    for pfad in ("/static/app.js", "/"):
        antwort = desktop.get(pfad)
        assert antwort.status_code == 200, pfad
        assert antwort.headers.get("cache-control") == "no-cache", pfad


def test_update_pruefung_im_gehosteten_betrieb_untaetig(hosted):
    stand = hosted.get("/api/update").json()
    assert stand["available"] is False
    assert stand["checked"] is False


def _stub_client(antwort_json, gefragt=None):
    import httpx

    class StubClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k):
            if gefragt is not None:
                gefragt.append(url)
            # Ohne zugehoerigen Request scheitert raise_for_status().
            return httpx.Response(200, json=antwort_json,
                                  request=httpx.Request("GET", url))
    return StubClient


def test_update_pruefung_fragt_github_releases(desktop, monkeypatch):
    """Ohne eigene Angabe gilt das neueste Release dieses Repositorys."""
    from app import main
    from app.config import LATEST_RELEASE_API, Settings

    gefragt = []
    monkeypatch.setattr(settings, "update_url", Settings.model_fields["update_url"].default)
    monkeypatch.setattr(main.httpx, "AsyncClient", _stub_client(
        {"tag_name": "v9.9.9", "html_url": "https://github.com/x/y/releases/tag/v9.9.9"},
        gefragt))
    stand = desktop.get("/api/update").json()
    assert gefragt == [LATEST_RELEASE_API]
    assert stand["available"] is True
    assert stand["latest"] == "9.9.9"
    assert stand["download_url"] == "https://github.com/x/y/releases/tag/v9.9.9"


def test_update_pruefung_versteht_die_schlichte_form(desktop, monkeypatch):
    from app import main

    monkeypatch.setattr(settings, "update_url", "https://example.org/latest.json")
    monkeypatch.setattr(main.httpx, "AsyncClient", _stub_client(
        {"version": "0.0.1", "download_url": "https://example.org/dl"}))
    stand = desktop.get("/api/update").json()
    assert stand["checked"] is True
    assert stand["available"] is False
    assert stand["download_url"] == "https://example.org/dl"


def test_abgeschaltete_update_pruefung_fragt_nicht(desktop, monkeypatch):
    """UPDATE_CHECK=false heißt: kein Aufruf nach draußen."""
    from app import main

    monkeypatch.setattr(settings, "update_check", False)

    def darf_nicht(*args, **kwargs):
        raise AssertionError("es wurde trotzdem angefragt")

    monkeypatch.setattr(main.httpx, "AsyncClient", darf_nicht)
    stand = desktop.get("/api/update").json()
    assert stand["checked"] is False
    assert stand["available"] is False


def test_update_pruefung_bleibt_bei_ausfall_stumm(desktop, monkeypatch):
    import httpx

    from app import main

    class KaputterClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise httpx.ConnectError("kein Netz")

    monkeypatch.setattr(settings, "update_url", "https://example.org/api/latest")
    monkeypatch.setattr(main.httpx, "AsyncClient", KaputterClient)
    stand = desktop.get("/api/update").json()
    # Offline ist kein Fehlerfall - es darf nur nichts angeboten werden.
    assert stand["available"] is False
    assert stand["checked"] is False


def test_version_stimmt_mit_pyproject_ueberein():
    import tomllib
    from pathlib import Path

    from app.version import __version__

    daten = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert daten["project"]["version"] == __version__


# -- Einstellungen je Benutzer -----------------------------------------------

def test_unbekannte_felder_landen_nicht_in_der_datei(hosted):
    """Sonst wäre die Ablage ein offener Eimer für alles, was der Browser schickt."""
    gespeichert = hosted.put("/api/prefs", json={
        "engine": "voxtral", "api_key": "geheim", "__proto__": "x",
    }).json()
    assert gespeichert == {"engine": "voxtral"}


def test_benutzername_wird_nicht_zum_pfad(hosted, tmp_path):
    """Ein Name wie ../../etc darf nicht aus dem Verzeichnis herausführen."""
    from app import userprefs
    userprefs.save(tmp_path / "prefs", "../../etc/passwd", {"engine": "x"})
    dateien = [p.name for p in (tmp_path / "prefs").iterdir()]
    assert dateien == [".._.._etc_passwd.json"]


def test_ohne_gespeicherte_einstellungen_kommt_ein_leeres_objekt(hosted):
    assert hosted.get("/api/prefs").json() == {}


# -- Datenschutz-Angabe ------------------------------------------------------

def test_jeder_dienst_sagt_etwas_zum_datenschutz(hosted):
    """Eine leere Zeile wäre schlimmer als eine grobe Angabe - deshalb gibt es
    eine Rückfallebene nach Diensttyp, auch für selbst geschriebene Einträge."""
    daten = hosted.get("/api/config").json()
    for eintrag in daten["engines"] + daten["llm_endpoints"]:
        assert eintrag["privacy"].strip(), eintrag


def test_eigener_eintrag_ohne_angabe_faellt_auf_den_typ_zurueck():
    from app.config import FREMD_PRIVACY, MISTRAL_PRIVACY, Endpoint
    assert Endpoint(name="x", type="mistral", url="u", model="m",
                    local=False).privacy_text == MISTRAL_PRIVACY
    assert Endpoint(name="x", url="u", model="m", local=True).privacy_text \
        == "Bleibt im eigenen Netz."
    assert Endpoint(name="x", url="u", model="m", local=False).privacy_text == FREMD_PRIVACY
    assert Endpoint(name="x", url="u", model="m", privacy="eigener Satz").privacy_text \
        == "eigener Satz"


# -- Nachgezogene Fehler (Review-Läufe 2026-09) ---------------------------------

def test_diktat_mit_stromdienst_nimmt_einen_dateidienst(hosted, monkeypatch):
    """Voxtral Realtime kennt nur den Strom. Das Diktat lief deshalb gehostet
    in der Standardeinstellung auf einen Fehler 500."""
    from app import main

    benutzt = []

    class Stub:
        async def transcribe(self, wav, filename="x.wav"):
            return "diktiert"

    echt = main.create_from_engine

    def merkend(engine, vocabulary=""):
        benutzt.append(engine.id)
        return Stub() if engine.id != "voxtral-realtime" else echt(engine, vocabulary)

    monkeypatch.setattr(main, "create_from_engine", merkend)
    sid = hosted.post("/api/sessions",
                      json={"engine": "voxtral-realtime"}).json()["id"]
    antwort = hosted.post(f"/api/sessions/{sid}/transcribe",
                          content=b"RIFF....WAVE")
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["text"] == "diktiert"
    assert benutzt == ["voxtral-realtime", "voxtral"]


def test_trigger_einstellungen_werden_gespeichert(hosted):
    """Sie fielen aus der Feldliste heraus - nach dem Neuladen galten wieder
    die Vorgaben, auf jedem Gerät."""
    hosted.put("/api/prefs",
               json={"trigger_seconds": 120, "trigger_chars": 2000})
    assert hosted.get("/api/prefs").json() == {
        "trigger_seconds": 120, "trigger_chars": 2000}


def test_schalter_bleiben_wahrheitswerte(tmp_path):
    """Als Text käme "False" zurück - und der ist in JavaScript wahr."""
    from app import userprefs

    userprefs.FELDER += ("probe",)
    try:
        userprefs.save(tmp_path, "anna", {"probe": False})
        assert userprefs.load(tmp_path, "anna")["probe"] is False
    finally:
        userprefs.FELDER = userprefs.FELDER[:-1]


def test_trigger_werte_werden_begrenzt(hosted):
    """Jeder Protokolllauf kostet - 0 oder negative Werte darf es nicht geben."""
    sid = hosted.post("/api/sessions", json={
        "engine": "voxtral", "trigger_seconds": 0, "trigger_chars": 999_999,
    }).json()["id"]
    session = sessions[sid]
    assert session.settings.trigger_seconds == 20.0
    assert session.settings.trigger_chars == 8000


# -- Upload am Stück -----------------------------------------------------

def test_upload_geht_am_stueck_an_den_dienst(hosted, monkeypatch):
    from app import main

    angekommen = {}

    class Stub:
        async def transcribe_file(self, data, filename, content_type, diarize=False):
            angekommen.update(n=len(data), name=filename, art=content_type)
            return [{"start": 0.0, "end": 4.0, "text": "Erster Satz ist kurz."},
                    {"start": 4.0, "end": 9.0, "text": "Zweiter Satz. " * 12},
                    {"start": 65.0, "end": 70.0, "text": "Dritter Satz."}]

    monkeypatch.setattr(main, "create_from_engine", lambda engine, vocabulary="": Stub())
    datei = b"\x00" * (2 * 1024 * 1024)  # über der 1-MB-Schwelle des Spoolings
    antwort = hosted.post("/api/transcribe?engine=voxtral",
                          content=datei, headers={"Content-Type": "audio/mpeg",
                                                  "X-Filename": "memo.m4a"})
    assert antwort.status_code == 200, antwort.text
    daten = antwort.json()
    assert angekommen == {"n": len(datei), "name": "memo.m4a", "art": "audio/mpeg"}
    # Kurze Segmente werden zu Absätzen mit der Zeit des ersten Segments.
    assert [e["seconds"] for e in daten["transcript"]] == [0.0, 65.0]
    assert daten["duration"] == 70.0


def test_upload_legt_nichts_auf_die_platte(hosted, monkeypatch):
    """Formular-Uploads spoolt Starlette ab 1 MB in eine temporäre Datei."""
    import tempfile

    from app import main

    def verboten(*a, **k):
        raise AssertionError("temporäre Datei angelegt")

    class Stub:
        async def transcribe_file(self, data, filename, content_type, diarize=False):
            return [{"start": 0.0, "end": 1.0, "text": "ok"}]

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", verboten)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", verboten)
    monkeypatch.setattr(main, "create_from_engine", lambda engine, vocabulary="": Stub())
    antwort = hosted.post("/api/transcribe?engine=voxtral",
                          content=b"\x01" * (3 * 1024 * 1024))
    assert antwort.status_code == 200, antwort.text


def test_zu_grosse_datei_wird_abgewiesen(hosted, monkeypatch):
    from app.main import settings as app_settings

    monkeypatch.setattr(app_settings, "max_upload_mb", 1)
    antwort = hosted.post("/api/transcribe?engine=voxtral",
                          content=b"\x00" * (2 * 1024 * 1024))
    assert antwort.status_code == 413
    assert "1 MB" in antwort.json()["detail"]


def test_stromdienst_nimmt_keine_datei(hosted):
    antwort = hosted.post("/api/transcribe?engine=voxtral-realtime", content=b"x")
    assert antwort.status_code == 400


def test_sprecher_unterscheiden_geht_an_voxtral(hosted, monkeypatch):
    """Der gewählte Dienst zählt dann nicht - Sprecher kann nur Voxtral."""
    from app import main

    benutzt = {}

    class Stub:
        async def transcribe_file(self, data, filename, content_type, diarize=False):
            benutzt["diarize"] = diarize
            return [{"start": 0.0, "end": 2.0, "text": "Wir fangen an.", "speaker": "speaker_1"},
                    {"start": 2.0, "end": 2.5, "text": "Ja.", "speaker": "speaker_2"},
                    {"start": 2.5, "end": 5.0, "text": "Gut, also.", "speaker": "speaker_1"}]

    def erzeuge(engine, vocabulary=""):
        benutzt["engine"] = engine.id
        return Stub()

    monkeypatch.setattr(main, "create_from_engine", erzeuge)
    antwort = hosted.post("/api/transcribe?engine=voxtral-realtime&diarize=true", content=b"x" * 100)
    assert antwort.status_code == 200, antwort.text
    assert benutzt == {"engine": "voxtral", "diarize": True}
    # Ein kurzer Einwurf bleibt eine eigene Zeile mit seinem Sprecher.
    assert [(e["speaker"], e["text"]) for e in antwort.json()["transcript"]] == [
        ("speaker_1", "Wir fangen an."), ("speaker_2", "Ja."), ("speaker_1", "Gut, also.")]


# -- Anmeldung über den Proxy --------------------------------------------------

@pytest.fixture
def per_proxy(monkeypatch, tmp_path):
    """Gehostet mit AUTH_PROXY: Benutzer und Gruppen kommen als Kopfzeilen."""
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "auth_proxy", True)
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-or-key")
    monkeypatch.setattr(settings, "transcription_engines_raw", json.dumps([
        {"id": "voxtral", "name": "Voxtral", "type": "mistral",
         "url": "https://api.mistral.ai", "model": "voxtral-mini-latest", "local": False},
        {"id": "openrouter-whisper", "name": "Whisper turbo", "type": "openai-compatible",
         "url": "https://openrouter.ai/api", "model": "openai/whisper-large-v3-turbo",
         "local": False, "key_ref": "openrouter", "restricted": True},
    ]))
    sessions.clear()
    owners.clear()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _als(name, gruppen="protokoll"):
    return {"Remote-User": name, "Remote-Groups": gruppen}


def test_proxy_ohne_kopfzeile_geschlossen(per_proxy):
    """Kommt die Anfrage nicht über den Proxy, bleibt die App zu - und fragt
    nicht nach Basic Auth, die es nicht gibt."""
    antwort = per_proxy.get("/api/config")
    assert antwort.status_code == 401
    assert "www-authenticate" not in antwort.headers


def test_proxy_kopfzeile_meldet_an(per_proxy):
    assert per_proxy.get("/api/config", headers=_als("anna")).status_code == 200


def test_proxy_ignoriert_basic_auth(per_proxy):
    assert per_proxy.get("/api/config", auth=("anna", "geheim")).status_code == 401


def test_proxy_sperre_gilt_auch_wenn_die_kennung_erraten_wird(per_proxy):
    """Die Oberfläche auszutricksen darf nicht reichen."""
    antwort = per_proxy.post("/api/sessions", headers=_als("anna"),
                             json={"engine": "openrouter-whisper", "output_type": "meeting"})
    assert antwort.status_code == 400
    assert "Unbekannte Engine" in antwort.json()["detail"]
    frei = per_proxy.post("/api/sessions", headers=_als("ben", "lp-alle"),
                          json={"engine": "openrouter-whisper", "output_type": "meeting"})
    assert frei.status_code == 200, frei.text


def test_proxy_grenze_gleichzeitiger_sitzungen(per_proxy, monkeypatch):
    """Hinter dem Proxy teilen sich mehrere Benutzer die Schlüssel - dort
    gilt die Höchstzahl je Benutzer."""
    monkeypatch.setattr(settings, "max_sessions_per_user", 1)
    erste = per_proxy.post("/api/sessions", headers=_als("anna"), json={"output_type": "meeting"})
    assert erste.status_code == 200
    sessions[erste.json()["id"]].running = True
    zweite = per_proxy.post("/api/sessions", headers=_als("anna"), json={"output_type": "meeting"})
    assert zweite.status_code == 429
    # Ein anderer Benutzer hat seine eigene Grenze.
    andere = per_proxy.post("/api/sessions", headers=_als("ben"), json={"output_type": "meeting"})
    assert andere.status_code == 200


def test_proxy_einstellungen_gehoeren_dem_benutzer(per_proxy):
    """Zwei Benutzer, zwei Einstellungen - und keiner sieht die des anderen."""
    per_proxy.put("/api/prefs", headers=_als("anna"),
                  json={"engine": "voxtral", "vocabulary": "Föhr"})
    per_proxy.put("/api/prefs", headers=_als("ben"), json={"engine": "voxtral-realtime"})
    assert per_proxy.get("/api/prefs", headers=_als("anna")).json() == {
        "engine": "voxtral", "vocabulary": "Föhr"}
    assert per_proxy.get("/api/prefs", headers=_als("ben")).json() == {
        "engine": "voxtral-realtime"}


def test_proxy_freigabe_ueber_gruppe(per_proxy):
    def ids(kopf):
        return [e["id"] for e in per_proxy.get("/api/config", headers=kopf).json()["engines"]]
    assert "openrouter-whisper" not in ids(_als("anna"))
    assert "openrouter-whisper" in ids(_als("ben", "protokoll,lp-openrouter-whisper"))
    # Beim Proxy entzogen heißt: ab der nächsten Anfrage weg.
    assert "openrouter-whisper" not in ids(_als("ben", "protokoll"))


def test_proxy_fremde_sitzung_nicht_erreichbar(per_proxy):
    angelegt = per_proxy.post("/api/sessions", headers=_als("anna"),
                              json={"output_type": "meeting"})
    assert angelegt.status_code == 200, angelegt.text
    session_id = angelegt.json()["id"]
    assert per_proxy.get(f"/api/sessions/{session_id}", headers=_als("anna")).status_code == 200
    assert per_proxy.get(f"/api/sessions/{session_id}", headers=_als("ben")).status_code == 404


def test_proxy_websocket_ohne_kopfzeile_abgewiesen(per_proxy):
    from starlette.websockets import WebSocketDisconnect
    angelegt = per_proxy.post("/api/sessions", headers=_als("anna"),
                              json={"output_type": "meeting"})
    session_id = angelegt.json()["id"]
    with pytest.raises(WebSocketDisconnect) as fehler:
        with per_proxy.websocket_connect(f"/api/sessions/{session_id}/stream"):
            pass
    assert fehler.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as fehler:
        with per_proxy.websocket_connect(f"/api/sessions/{session_id}/stream",
                                         headers=_als("ben")):
            pass
    assert fehler.value.code == 4404
