"""Die Neuigkeiten müssen zur Version passen, sonst sieht niemand das Fenster."""

import json
from pathlib import Path

from app.version import __version__, parse

DATEI = Path(__file__).parent.parent / "static" / "neuigkeiten.json"


def test_neuigkeiten_sind_gueltig_und_absteigend():
    abschnitte = json.loads(DATEI.read_text(encoding="utf-8"))
    versionen = [parse(a["version"]) for a in abschnitte]
    assert versionen == sorted(versionen, reverse=True)
    for abschnitt in abschnitte:
        assert abschnitt["items"]
        for punkt in abschnitt["items"]:
            assert punkt["title"] and punkt["text"]
            assert punkt.get("only") in (None, "hosted", "desktop", "admin")


def test_jede_version_hat_einen_abschnitt():
    """Wer die Version hochsetzt, schreibt auch auf, was neu ist."""
    abschnitte = json.loads(DATEI.read_text(encoding="utf-8"))
    assert parse(__version__) in [parse(a["version"]) for a in abschnitte]
