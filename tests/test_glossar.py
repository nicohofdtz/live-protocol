"""Korrekturen aus dem Transkript und Glossare."""

from __future__ import annotations

from pathlib import Path

from app import summary, userprefs
from app.config import Settings
from app.output_types import load_output_types
from app.session import LiveSession
from tests.test_session import StubChat, StubTranscription


def _sitzung(tmp_path) -> LiveSession:
    return LiveSession(output_type=load_output_types()["meeting"], strategy_name="rewrite",
                       transcription=StubTranscription(), chat=StubChat(),
                       settings=Settings(sessions_dir=tmp_path), live_protocol=False)


def test_korrektur_gilt_rueckwirkend_und_fuer_neues(tmp_path):
    s = _sitzung(tmp_path)
    s._add_entry("Frau Meier kommt vom Meierhof.", 0)
    assert s.correct("Meier", "Maier", vocabulary="Maier, Kubernetes") == 1
    # Nur das ganze Wort - der Meierhof bleibt.
    assert s.transcript[0]["text"] == "Frau Maier kommt vom Meierhof."
    s._add_entry("Danke, Meier.", 5)
    assert s.transcript[1]["text"] == "Danke, Maier."
    # Die Erkennung bekommt die neue Begriffsliste gleich mit (der Stub hat
    # das Feld nicht von Haus aus, die echten Dienste schon).
    s.transcription.vocabulary = ""
    s.correct("Kuhbernetes", "Kubernetes", vocabulary="Maier, Kubernetes")
    assert s.transcription.vocabulary == "Maier, Kubernetes"


def test_leere_oder_gleiche_korrektur_aendert_nichts(tmp_path):
    s = _sitzung(tmp_path)
    s._add_entry("Hallo", 0)
    assert s.correct("", "x") == 0 and s.correct("Hallo", "Hallo") == 0
    assert s.corrections == []


def test_glossar_geht_in_die_zusammenfassung():
    system, _ = summary.build_prompt(load_output_types()["meeting"],
                                     [{"text": "x", "seconds": 0}], glossar="Maier,\nKubernetes")
    assert "Maier, Kubernetes" in system
    ohne, _ = summary.build_prompt(load_output_types()["meeting"], [{"text": "x", "seconds": 0}])
    assert "richtig geschrieben" not in ohne


def test_glossare_passen_in_die_einstellungen(tmp_path: Path):
    glossare = [{"id": f"g{i}", "name": f"Liste {i}",
                 "terms": [f"Begriff{j}" for j in range(150)]} for i in range(3)]
    stand = userprefs.save(tmp_path, "anna", {"glossaries": glossare,
                                               "active_glossaries": ["g0", "g2"]})
    assert stand["glossaries"] == glossare
    assert userprefs.load(tmp_path, "anna")["active_glossaries"] == ["g0", "g2"]
