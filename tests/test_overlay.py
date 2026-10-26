"""Overlay: eigene Dateien einer Instanz, ohne den Kern anzufassen."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import RELEASES_URL, settings
from app.main import app, owners, sessions


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "deployment_mode", "desktop")
    monkeypatch.setattr(settings, "sessions_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings, "user_prefs_dir", tmp_path / "prefs")
    monkeypatch.setattr(settings, "overlay_dir", None)
    monkeypatch.setattr(settings, "banner_text", "")
    monkeypatch.setattr(settings, "update_check", False)
    sessions.clear()
    owners.clear()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def overlay(monkeypatch, tmp_path):
    ordner = tmp_path / "overlay"
    ordner.mkdir()
    monkeypatch.setattr(settings, "overlay_dir", ordner)
    return ordner


def test_ohne_overlay_keine_seiten_und_kein_hinweis(client):
    config = client.get("/api/config").json()
    assert config["pages"] == []
    assert config["banner"] == ""
    assert client.get("/impressum").status_code == 404
    assert client.get("/datenschutz").status_code == 404


def test_seiten_aus_dem_overlay_mit_fusszeilenlink(client, overlay):
    (overlay / "impressum.html").write_text(
        "<html><head><title>Impressum – Meine Instanz</title></head>"
        "<body><h1>Impressum</h1></body></html>", encoding="utf-8")
    (overlay / "datenschutz.html").write_text(
        "<title>Datenschutz</title><h1>Datenschutz</h1>", encoding="utf-8")

    config = client.get("/api/config").json()
    assert config["pages"] == [{"path": "/datenschutz", "title": "Datenschutz"},
                               {"path": "/impressum", "title": "Impressum"}]
    antwort = client.get("/impressum")
    assert antwort.status_code == 200 and "<h1>Impressum</h1>" in antwort.text
    # Was es nicht gibt, bleibt 404 - auch im Overlay.
    assert client.get("/agb").status_code == 404


def test_overlay_ersetzt_statische_dateien(client, overlay):
    (overlay / "style.css").write_text("body { color: hotpink; }", encoding="utf-8")
    (overlay / "logo.svg").write_text("<svg/>", encoding="utf-8")
    assert "hotpink" in client.get("/static/style.css").text
    assert client.get("/static/logo.svg").text == "<svg/>"
    # Alles andere kommt weiter aus static/.
    assert "function" in client.get("/static/app.js").text
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"


def test_eigene_startseite(client, overlay):
    (overlay / "index.html").write_text("<p>eigene Startseite</p>", encoding="utf-8")
    assert client.get("/").text == "<p>eigene Startseite</p>"
    # Die Startseite ist keine zusätzliche Seite.
    assert client.get("/api/config").json()["pages"] == []
    assert client.get("/index").status_code == 404


def test_hinweis_aus_dem_overlay_geht_vor(client, overlay, monkeypatch):
    monkeypatch.setattr(settings, "banner_text", "aus der Konfiguration")
    assert client.get("/api/config").json()["banner"] == "aus der Konfiguration"
    (overlay / "banner.html").write_text('Bitte <a href="/impressum">vorher anfragen</a>.\n',
                                         encoding="utf-8")
    config = client.get("/api/config").json()
    assert config["banner"] == 'Bitte <a href="/impressum">vorher anfragen</a>.'
    assert client.get("/banner").status_code == 404


def test_hinweis_aus_der_konfiguration_ist_klartext(client, monkeypatch):
    monkeypatch.setattr(settings, "banner_text", "Test <b>& so</b>")
    assert client.get("/api/config").json()["banner"] == "Test &lt;b&gt;&amp; so&lt;/b&gt;"


def test_kein_ausbruch_aus_dem_overlay(client, overlay, tmp_path):
    (tmp_path / "geheim.html").write_text("geheim", encoding="utf-8")
    for pfad in ("/..%2fgeheim", "/static/..%2f..%2fgeheim.html", "/Geheim"):
        antwort = client.get(pfad)
        assert antwort.status_code == 404, pfad
        assert "geheim" not in antwort.text


def test_zustimmungshinweis_abschaltbar(client, monkeypatch):
    assert client.get("/api/config").json()["consent_hint"] is True
    monkeypatch.setattr(settings, "consent_hint", False)
    assert client.get("/api/config").json()["consent_hint"] is False


def test_hinweis_auf_lokale_fassung_zeigt_auf_die_releases(monkeypatch, tmp_path):
    from app.config import Settings
    assert Settings.model_fields["download_url"].default == RELEASES_URL
    assert RELEASES_URL.startswith("https://github.com/")
