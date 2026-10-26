"""Gespeicherte Sitzungen (Tresor), Freigaben je Gruppe, Verbrauch je Benutzer."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import usage, vault
from app.config import Endpoint, Engine, settings
from app.llm import ChatClient, Completion
from app.main import app, owners, sessions
from tests.test_api import _ohne_anbieter

SCHLUESSEL = {"salt": "c2FsdA==", "wrapped": "d3JhcHBlZA==", "iterations": 600_000}


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    _ohne_anbieter(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    monkeypatch.setattr(settings, "auth_proxy", True)
    monkeypatch.setattr(settings, "sessions_dir", tmp_path)
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    monkeypatch.setattr(settings, "vault_dir", tmp_path / "vault")
    monkeypatch.setattr(settings, "usage_dir", tmp_path / "usage")
    monkeypatch.setattr(settings, "vault_quota_mb", 1)
    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-or-key")
    sessions.clear()
    owners.clear()
    with TestClient(app, base_url="https://testserver") as client:
        yield client


def _als(name, gruppen="protokoll"):
    return {"Remote-User": name, "Remote-Groups": gruppen}


# -- Tresor --------------------------------------------------------------------

def test_tresor_braucht_zuerst_einen_schluessel(proxy):
    antwort = proxy.put("/api/vault/items/" + "a" * 16, headers=_als("anna"), content=b"x")
    assert antwort.status_code == 409


def test_tresor_rundlauf_und_trennung_der_nutzer(proxy):
    assert proxy.put("/api/vault/key", headers=_als("anna"), json=SCHLUESSEL).status_code == 200
    kennung = "0123456789abcdef"
    kopf = {**_als("anna"), "X-Meta": "bWV0YQ=="}
    assert proxy.put(f"/api/vault/items/{kennung}", headers=kopf,
                     content=b"chiffrat").status_code == 200

    stand = proxy.get("/api/vault", headers=_als("anna")).json()
    assert stand["key"]["salt"] == SCHLUESSEL["salt"]
    assert [(i["id"], i["size"], i["meta"]) for i in stand["items"]] == [(kennung, 8, "bWV0YQ==")]
    assert proxy.get(f"/api/vault/items/{kennung}", headers=_als("anna")).content == b"chiffrat"

    # Ein anderer Nutzer sieht weder Schlüssel noch Eintrag.
    fremd = proxy.get("/api/vault", headers=_als("ben")).json()
    assert fremd["key"] is None and fremd["items"] == []
    assert proxy.get(f"/api/vault/items/{kennung}", headers=_als("ben")).status_code == 404

    assert proxy.delete(f"/api/vault/items/{kennung}", headers=_als("anna")).status_code == 200
    assert proxy.get("/api/vault", headers=_als("anna")).json()["items"] == []


def test_tresor_schluessel_wird_nicht_still_ueberschrieben(proxy):
    proxy.put("/api/vault/key", headers=_als("anna"), json=SCHLUESSEL)
    assert proxy.put("/api/vault/key", headers=_als("anna"), json=SCHLUESSEL).status_code == 409
    neu = {**SCHLUESSEL, "wrapped": "bmV1", "replace": True}
    assert proxy.put("/api/vault/key", headers=_als("anna"), json=neu).status_code == 200


def test_tresor_grenze_und_kennung(proxy):
    proxy.put("/api/vault/key", headers=_als("anna"), json=SCHLUESSEL)
    zu_gross = b"x" * (1024 * 1024 + 1)
    assert proxy.put("/api/vault/items/" + "b" * 16, headers=_als("anna"),
                     content=zu_gross).status_code == 413
    assert proxy.put("/api/vault/items/..%2Fkey", headers=_als("anna"),
                     content=b"x").status_code in (400, 404)


def test_tresor_leeren(proxy):
    proxy.put("/api/vault/key", headers=_als("anna"), json=SCHLUESSEL)
    proxy.put("/api/vault/items/" + "c" * 16, headers=_als("anna"), content=b"x")
    assert proxy.delete("/api/vault", headers=_als("anna")).status_code == 200
    assert proxy.get("/api/vault", headers=_als("anna")).json()["key"] is None


def test_tresor_gibt_es_am_eigenen_rechner_nicht(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deployment_mode", "desktop")
    with TestClient(app) as client:
        assert client.get("/api/vault").status_code == 404
        assert client.get("/api/config").json()["vault"] is False


def test_modulebene_verwirft_fremde_kennungen(tmp_path):
    with pytest.raises(vault.VaultError):
        vault.item_path(tmp_path, "anna", "../../etc/passwd")


# -- Freigaben je Gruppe -----------------------------------------------------------

def test_gesperrtes_zusammenfassungsmodell_nur_mit_gruppe(proxy, monkeypatch):
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    modelle = settings._vorgabe_summary()
    modelle[1]["restricted"] = True
    monkeypatch.setattr(settings, "summary_models_raw", json.dumps(modelle))

    def ids(gruppen):
        config = proxy.get("/api/config", headers=_als("anna", gruppen)).json()
        return [m["id"] for m in config["summary_models"]]

    assert ids("protokoll") == ["eu"]
    assert ids("protokoll,lp-eu-schnell") == ["eu", "eu-schnell"]
    assert ids("protokoll,lp-alle") == ["eu", "eu-schnell"]
    antwort = proxy.post("/api/summarize", headers=_als("anna"), json={
        "model": "eu-schnell", "transcript": [{"text": "x", "seconds": 0}]})
    assert antwort.status_code == 400


def test_gesperrtes_live_modell_nur_mit_gruppe(proxy, monkeypatch):
    endpunkte = settings._vorgabe_llm()
    for e in endpunkte:
        e["restricted"] = e["name"] == "openai-compatible"
    monkeypatch.setattr(settings, "llm_endpoints_raw", json.dumps(endpunkte))
    ohne = proxy.get("/api/config", headers=_als("anna")).json()["llm_endpoints"]
    mit = proxy.get("/api/config",
                    headers=_als("anna", "protokoll,lp-openai-compatible")).json()["llm_endpoints"]
    assert [e["name"] for e in ohne] == ["mistral"]
    assert [e["name"] for e in mit] == ["openai-compatible", "mistral"]
    assert all(e["label"] for e in mit)


# -- Verbrauch --------------------------------------------------------------------

def test_kosten_aus_listenpreisen():
    e = Endpoint(name="x", url="u", model="m", price_in=1.0, price_out=2.0)
    assert usage.cost_tokens(e, 1_000_000, 500_000) == pytest.approx(2.0)
    t = Engine(id="t", name="t", url="u", model="m", price_hour=0.4)
    assert usage.cost_audio(t, 1800) == pytest.approx(0.2)


def test_zusammenfassung_wird_je_nutzer_erfasst(proxy, monkeypatch):
    async def antwort(self, endpoint, system, user, json_mode, temperature):
        return Completion("# Ergebnis", endpoint.name, 1_000_000, 100_000)

    monkeypatch.setattr(ChatClient, "_call", antwort)
    r = proxy.post("/api/summarize", headers=_als("ben"), json={
        "model": "eu", "transcript": [{"text": "Hallo", "seconds": 0}]})
    assert r.status_code == 200, r.text

    # Nur Administratoren sehen die Übersicht.
    assert proxy.get("/api/usage", headers=_als("ben")).status_code == 403
    uebersicht = proxy.get("/api/usage", headers=_als("anna", "protokoll,admins")).json()
    [ben] = uebersicht["users"]
    assert ben["user"] == "ben" and ben["prompt_tokens"] == 1_000_000
    assert ben["cost"] == pytest.approx(1.54 + 0.484)
    assert ben["resources"][0]["name"].startswith("zusammenfassung")
    assert proxy.get("/api/config", headers=_als("anna", "admins")).json()["admin"] is True


def test_upload_zaehlt_audiominuten(proxy, monkeypatch):
    from app import transcription

    async def datei(self, daten, name, art, diarize=False):
        return [{"start": 0.0, "end": 600.0, "text": "Hallo"}]

    for klasse in vars(transcription).values():
        if isinstance(klasse, type) and hasattr(klasse, "transcribe_file"):
            monkeypatch.setattr(klasse, "transcribe_file", datei)
    r = proxy.post("/api/transcribe?engine=voxtral", headers=_als("ben"), content=b"x")
    assert r.status_code == 200, r.text
    zeilen = [json.loads(z) for z in
              next((settings.usage_dir).glob("*.jsonl")).read_text().splitlines()]
    assert zeilen[0]["user"] == "ben" and zeilen[0]["seconds"] == 600.0
    assert zeilen[0]["cost"] == pytest.approx(0.198 / 6)


def test_am_eigenen_rechner_wird_nichts_erfasst(tmp_path):
    usage.record(None, "local", "x", "y", cost=1.0)
    assert not list(tmp_path.iterdir())


def test_monatsgrenze_sperrt_bezahlte_aufrufe(proxy, monkeypatch):
    monkeypatch.setattr(settings, "usage_limit_usd", 1.0)
    usage.record(settings.usage_dir, "ben", "datei", "Voxtral", seconds=60, cost=1.2)
    antwort = proxy.post("/api/summarize", headers=_als("ben"), json={
        "model": "eu", "transcript": [{"text": "x", "seconds": 0}]})
    assert antwort.status_code == 429 and "Monatskontingent" in antwort.json()["detail"]
    assert proxy.post("/api/transcribe?engine=voxtral", headers=_als("ben"),
                      content=b"x").status_code == 429
    assert proxy.get("/api/config", headers=_als("ben")).json()["quota"]["used"] == 1.2
    # Eigene Grenze über die Gruppe, keine Grenze für admins.
    kopf = _als("ben", "protokoll,lp-limit-5")
    assert proxy.get("/api/config", headers=kopf).json()["quota"]["limit"] == 5.0
    assert proxy.get("/api/config", headers=_als("ben", "protokoll,admins")).json()["quota"] is None


def test_korrektur_in_laufender_sitzung(proxy):
    angelegt = proxy.post("/api/sessions", headers=_als("anna"),
                          json={"engine": "voxtral", "vocabulary": "Alt"}).json()
    sitzung = sessions[angelegt["id"]]
    sitzung._add_entry("Frau Meier spricht.", 0)
    antwort = proxy.post(f"/api/sessions/{angelegt['id']}/correct", headers=_als("anna"),
                         json={"alt": "Meier", "neu": "Maier", "vocabulary": "Alt, Maier"})
    assert antwort.json() == {"replaced": 1, "vocabulary_live": True}
    assert sitzung.transcript[0]["text"] == "Frau Maier spricht."
    assert sitzung.transcription.vocabulary == "Alt, Maier"
    # Fremde Sitzungen bleiben unerreichbar.
    assert proxy.post(f"/api/sessions/{angelegt['id']}/correct", headers=_als("ben"),
                      json={"alt": "a", "neu": "b"}).status_code == 404


def test_meine_daten_herunterladen_und_loeschen(proxy):
    proxy.put("/api/prefs", headers=_als("ben"), json={"glossaries": [
        {"id": "g1", "name": "Verein", "terms": ["Maier"]}]})
    usage.record(settings.usage_dir, "ben", "datei", "Voxtral", seconds=60, cost=0.01)
    usage.record(settings.usage_dir, "anna", "datei", "Voxtral", seconds=60, cost=0.01)
    proxy.put("/api/vault/key", headers=_als("ben"), json=SCHLUESSEL)
    daten = proxy.get("/api/me/export", headers=_als("ben")).json()
    assert daten["settings"]["glossaries"][0]["terms"] == ["Maier"]
    assert [z["user"] for z in daten["usage"]] == ["ben"]
    assert proxy.delete("/api/me", headers=_als("ben")).status_code == 200
    assert proxy.get("/api/prefs", headers=_als("ben")).json() == {}
    assert proxy.get("/api/vault", headers=_als("ben")).json()["key"] is None


def test_alter_verbrauch_verfaellt(tmp_path):
    (tmp_path / "2020-01.jsonl").write_text("{}\n")
    (tmp_path / "2099-01.jsonl").write_text("{}\n")
    assert usage.prune(tmp_path) == ["2020-01"]
    assert [p.name for p in tmp_path.iterdir()] == ["2099-01.jsonl"]
