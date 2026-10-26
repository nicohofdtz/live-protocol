"""Anbieter: Mistral und ein OpenAI-kompatibler Dienst, mit Vorlagen."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import (OPENROUTER_PRIVACY, PRESETS, Settings, detect_preset,
                        normalize_base_url, settings)
from app.main import app, owners, sessions
from tests.test_api import _ohne_anbieter


def _settings(**werte) -> Settings:
    return Settings(_env_file=None, **werte)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Server-Betrieb ohne Schlüssel - jeder Test setzt seine eigenen."""
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "mistral_api_key", None)
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    sessions.clear()
    owners.clear()
    with TestClient(app) as client:
        yield client


@pytest.fixture
def desktop(monkeypatch, tmp_path):
    from app import localconfig
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "desktop")
    monkeypatch.setattr(settings, "mistral_api_key", None)
    monkeypatch.setattr(settings, "update_check", False)
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(localconfig, "config_file", lambda: tmp_path / "config.json")
    with TestClient(app) as client:
        yield client


# -- Nur ein Schlüssel genügt -----------------------------------------------------

def test_nur_mistral_kann_alles(server, monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "m-key")
    config = server.get("/api/config").json()
    engines = {e["id"]: e for e in config["engines"]}
    assert list(engines) == ["voxtral-realtime", "voxtral"]
    assert engines["voxtral-realtime"]["streaming"] is True
    assert engines["voxtral"]["diarize"] is True and engines["voxtral"]["vocabulary"] is True
    assert [e["name"] for e in config["llm_endpoints"]] == ["mistral"]
    assert [m["id"] for m in config["summary_models"]] == ["eu", "eu-schnell"]


def test_nur_openai_kompatibel_kann_alles_ausser_mistral_eigenem(server, monkeypatch):
    monkeypatch.setattr(settings, "openai_compat_preset", "openai")
    monkeypatch.setattr(settings, "openai_compat_api_key", "sk-test")
    for feld in ("live", "summary", "transcription"):
        monkeypatch.setattr(settings, f"openai_compat_{feld}_model", f"modell-{feld}")
    config = server.get("/api/config").json()
    assert [e["id"] for e in config["engines"]] == ["openai-compatible"]
    assert config["engines"][0]["diarize"] is False
    assert config["engines"][0]["configured"] is True
    assert config["defaults"]["engine"] == "openai-compatible"
    assert [e["name"] for e in config["llm_endpoints"]] == ["openai-compatible"]
    assert [m["id"] for m in config["summary_models"]] == ["openai-compatible"]
    assert "USA" in config["summary_models"][0]["privacy"]
    # Sitzung, Live-Protokoll und Zusammenfassung lassen sich anlegen.
    antwort = server.post("/api/sessions", json={"llm_endpoint": "openai-compatible",
                                                 "live_protocol": True})
    assert antwort.status_code == 200, antwort.text
    # Sprecher unterscheiden kann nur Voxtral.
    assert server.post("/api/transcribe?diarize=true", content=b"x").status_code == 400


def test_zusammenfassung_geht_an_den_eingetragenen_anbieter(server, monkeypatch):
    from app.llm import ChatClient, Completion

    monkeypatch.setattr(settings, "openai_compat_base_url", "https://llm.example.org/v1/")
    monkeypatch.setattr(settings, "openai_compat_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_compat_summary_model", "mein-modell")
    gefragt = {}

    async def antwort(self, endpoint, system, user, json_mode, temperature):
        gefragt.update(url=endpoint.url, model=endpoint.model, key=endpoint.api_key)
        return Completion("# Ergebnis", endpoint.name, 10, 10)

    monkeypatch.setattr(ChatClient, "_call", antwort)
    r = server.post("/api/summarize", json={"model": "openai-compatible",
                                            "transcript": [{"text": "Hallo", "seconds": 0}]})
    assert r.status_code == 200, r.text
    # Ein "/v1" am Ende wird abgeschnitten, der Client hängt es selbst an.
    assert gefragt == {"url": "https://llm.example.org", "model": "mein-modell",
                       "key": "sk-test"}


# -- Vorlagen ---------------------------------------------------------------------

def test_vorlagen_fuellen_nur_vor():
    anbieter = _settings(openai_compat_preset="openrouter", openai_compat_api_key="k",
                         openai_compat_live_model="eigenes/modell").provider
    assert anbieter.url == PRESETS["openrouter"]["base_url"]
    assert anbieter.live_model == "eigenes/modell"
    assert anbieter.summary_model == PRESETS["openrouter"]["summary_model"]


def test_alle_vorlagen_bekannt_und_beschrieben():
    assert set(PRESETS) == {"mistral", "openrouter", "openai", "ollama", "custom"}
    for name, vorlage in PRESETS.items():
        assert vorlage["name"] and vorlage["privacy"], name
    # Wer Daten in die USA schickt, sagt es sichtbar.
    assert "USA" in PRESETS["openrouter"]["note"]
    assert "USA" in PRESETS["openai"]["note"]


def test_ollama_ohne_schluessel_im_eigenen_netz():
    s = _settings(openai_compat_preset="ollama")
    [live] = [e for e in s.llm_endpoints if e.name == "openai-compatible"]
    assert live.local and live.ready and live.disable_thinking
    assert live.url == "http://localhost:11434"
    assert "eigenen Netz" in live.privacy_text
    assert [m.id for m in s.summary_models] == ["openai-compatible"]
    # Ollama transkribiert nicht - ohne Modell kein Eintrag.
    assert "openai-compatible" not in [e.id for e in s.transcription_engines]


def test_vorlage_aus_der_adresse():
    assert detect_preset("https://openrouter.ai/api/v1") == "openrouter"
    assert detect_preset("https://api.openai.com/v1") == "openai"
    assert detect_preset("https://api.eu.mistral.ai") == "mistral"
    assert detect_preset("http://10.0.0.20:11434") == "ollama"
    assert detect_preset("https://llm.example.org") == "custom"
    assert normalize_base_url(" https://x.org/v1/ ") == "https://x.org"


def test_eigener_dienst_im_eigenen_netz_braucht_keinen_schluessel():
    s = _settings(openai_compat_base_url="http://10.0.0.5:8000",
                  openai_compat_live_model="m")
    [live] = [e for e in s.llm_endpoints if e.name == "openai-compatible"]
    assert live.local and live.ready


def test_cloud_dienst_ohne_schluessel_steht_nicht_zur_wahl():
    s = _settings(openai_compat_base_url="https://llm.example.org",
                  openai_compat_live_model="m", openai_compat_summary_model="m",
                  deployment_mode="hosted")
    assert s.summary_models == []
    assert s.llm_endpoints == []


# -- Alte Konfiguration -------------------------------------------------------------

def test_alter_openrouter_schluessel_gilt_weiter():
    s = _settings(openrouter_api_key="sk-or", deployment_mode="hosted")
    anbieter = s.provider
    assert anbieter.preset == "openrouter" and anbieter.api_key == "sk-or"
    [live] = s.llm_endpoints
    assert live.extra["provider"]["zdr"] is True
    assert live.privacy_text == OPENROUTER_PRIVACY
    [whisper] = s.transcription_engines
    assert whisper.model == "openai/whisper-large-v3-turbo" and whisper.api_key == "sk-or"


def test_eigene_json_liste_mit_altem_key_ref():
    eintrag = {"id": "w", "name": "W", "type": "openai-compatible", "url": "https://x",
               "model": "m", "local": False, "key_ref": "openrouter"}
    s = _settings(openrouter_api_key="sk-or", TRANSCRIPTION_ENGINES=json.dumps([eintrag]))
    [engine] = s.transcription_engines
    assert engine.api_key == "sk-or"


def test_transcription_url_ist_der_alte_name(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_URL", "http://127.0.0.1:9999")
    assert _settings().whisper_url == "http://127.0.0.1:9999"
    monkeypatch.setenv("WHISPER_URL", "http://127.0.0.1:7777")
    assert _settings().whisper_url == "http://127.0.0.1:7777"


# -- Einstellungen der Desktop-Fassung ----------------------------------------------

def test_desktop_anbieter_eintragen(desktop):
    vorher = desktop.get("/api/config").json()
    assert "openai-compatible" not in [m["id"] for m in vorher["summary_models"]]
    assert set(vorher["provider_presets"]) == set(PRESETS)

    antwort = desktop.put("/api/settings", json={
        "mistral_api_key": "m-key",
        "openai_compat": {"preset": "openrouter", "base_url": "https://openrouter.ai/api",
                          "api_key": "sk-or-test", "live_model": "google/gemma-4-26b-a4b-it",
                          "summary_model": "google/gemma-4-26b-a4b-it",
                          "transcription_model": ""}})
    assert antwort.status_code == 200, antwort.text
    stand = antwort.json()
    assert "sk-or-test" not in antwort.text and "m-key" not in antwort.text
    assert stand["mistral_api_key_set"] is True
    assert stand["openai_compat"]["api_key_set"] is True
    assert stand["openai_compat"]["preset"] == "openrouter"

    nachher = desktop.get("/api/config").json()
    assert [m["id"] for m in nachher["summary_models"]] == ["eu", "eu-schnell",
                                                            "openai-compatible"]
    assert "openai-compatible" in [e["name"] for e in nachher["llm_endpoints"]]

    # Ohne Schlüssel im zweiten Speichern bleibt der alte stehen.
    desktop.put("/api/settings", json={"openai_compat": {
        "preset": "openrouter", "base_url": "https://openrouter.ai/api",
        "live_model": "anderes/modell"}})
    from app import localconfig
    assert localconfig.load()["openai_compat"]["api_key"] == "sk-or-test"
    # Leeren entfernt den Anbieter.
    desktop.put("/api/settings", json={"openai_compat": None})
    assert "openai_compat" not in localconfig.load()


def test_desktop_einstellungen_pruefen_die_felder(desktop):
    assert desktop.put("/api/settings", json={"openrouter_api_key": "x"}).status_code == 400
    assert desktop.put("/api/settings", json={
        "openai_compat": {"preset": "irgendwas"}}).status_code == 400
    assert desktop.put("/api/settings", json={
        "openai_compat": {"base_url": "x", "geheim": "y"}}).status_code == 400


def test_alte_lokale_einstellungen_gelten_weiter(desktop, tmp_path):
    """Frühere Fassungen speicherten einen OpenRouter-Schlüssel und einen
    eigenen Endpunkt fürs Live-Protokoll."""
    from app import localconfig
    localconfig.save({"openrouter_api_key": "sk-or-alt"})
    config = desktop.get("/api/config").json()
    assert config["engines"][1]["id"] == "openai-compatible"
    assert config["engines"][1]["configured"] is True

    localconfig.save({"custom_llm": {"url": "http://localhost:11434",
                                     "model": "qwen3.5:9b"}})
    config = desktop.get("/api/config").json()
    live = next(e for e in config["llm_endpoints"] if e["name"] == "openai-compatible")
    assert live["model"] == "qwen3.5:9b" and live["local"] is True


def test_alter_schluessel_geht_beim_speichern_in_den_anbieter_ueber(desktop):
    from app import localconfig
    localconfig.save({"openrouter_api_key": "sk-or-alt"})
    antwort = desktop.put("/api/settings", json={"openai_compat": {
        "preset": "openrouter", "base_url": "https://openrouter.ai/api",
        "live_model": "google/gemma-4-26b-a4b-it"}})
    assert antwort.status_code == 200
    gespeichert = localconfig.load()
    assert gespeichert["openai_compat"]["api_key"] == "sk-or-alt"
    assert "openrouter_api_key" not in gespeichert


def test_openai_vorlage_ohne_modellnamen_bietet_nichts_an():
    """Ohne eingetragene Modelle entsteht kein Eintrag, der beim Klick scheitert."""
    s = _settings(openai_compat_preset="openai", openai_compat_api_key="sk",
                  deployment_mode="hosted")
    assert not s.provider.configured
    assert s.llm_endpoints == [] and s.summary_models == []
    assert "eintragen" in PRESETS["openai"]["note"]


def test_openrouter_fasst_mit_glm_zusammen():
    s = _settings(openai_compat_preset="openrouter", openai_compat_api_key="k",
                  deployment_mode="hosted")
    [modell] = s.summary_models
    assert modell.endpoint.model == "z-ai/glm-5.3"
    assert (modell.endpoint.price_in, modell.endpoint.price_out) == (0.079, 3.2)
    assert modell.cost
    assert [e.model for e in s.llm_endpoints] == ["google/gemma-4-26b-a4b-it"]
