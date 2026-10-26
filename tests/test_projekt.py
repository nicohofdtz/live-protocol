"""Das Projekt-ZIP: gebaut im Browser-Code, geprüft von außen.

Der Code liegt in static/projekt.js und läuft im Browser. Der Test fährt ihn
mit Node und prüft das Ergebnis mit Pythons zipfile - ein ZIP, das nur der
eigene Leser versteht, wäre keins. Ohne Node wird übersprungen.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

SKRIPT = Path(__file__).with_name("projekt_rundlauf.js")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node fehlt")


def _node(*args: str) -> str:
    return subprocess.run(["node", str(SKRIPT), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_projekt_ist_ein_gueltiges_zip(tmp_path):
    datei = tmp_path / "projekt.zip"
    _node("schreiben", str(datei))
    with zipfile.ZipFile(datei) as z:
        assert z.testzip() is None, "Prüfsumme falsch"
        assert set(z.namelist()) == {"projekt.json", "aufnahme.mp3"}
        stand = json.loads(z.read("projekt.json"))
    assert stand["format"] == "live-protocol-projekt" and stand["version"] == 1
    assert [e["id"] for e in stand["transcript"]] == ["z1", "z2", "z3"]
    assert stand["transcript"][1]["gap"] is True


def test_rundlauf_ergibt_denselben_stand(tmp_path):
    datei = tmp_path / "projekt.zip"
    _node("schreiben", str(datei))
    ergebnis = json.loads(_node("lesen", str(datei)))
    stand = ergebnis["stand"]
    assert stand["context"] == {"topic": "Sommerfest", "participants": "Martina, Jens"}
    assert stand["feedback"] == ["Namen ausschreiben"]
    assert stand["summary"]["markdown"].endswith("| Hüpfburg | Martina |")
    assert stand["transcript"][2]["text"] == "Die Hüpfburg übernimmt Martina."
    assert stand["transcript"][2]["speaker"] == "speaker_2"
    assert "speaker" not in stand["transcript"][0]
    assert stand["speakers"] == {"speaker_2": {"name": "Martina", "mehrere": False}}
    assert ergebnis["audio"]["name"] == "aufnahme.mp3"
    assert ergebnis["audio"]["laenge"] == 300000
    erwartet = sum(i % 251 for i in range(300000)) % 1000003
    assert ergebnis["audio"]["pruef"] == erwartet


def test_neu_gepacktes_zip_wird_gelesen(tmp_path):
    """Wer das Projekt mit einem anderen Werkzeug neu packt, bekommt deflate."""
    original, neu = tmp_path / "a.zip", tmp_path / "b.zip"
    _node("schreiben", str(original))
    with zipfile.ZipFile(original) as a, zipfile.ZipFile(neu, "w", zipfile.ZIP_DEFLATED) as b:
        for name in a.namelist():
            b.writestr(name, a.read(name))
    assert json.loads(_node("lesen", str(neu)))["audio"]["laenge"] == 300000


def test_fremde_und_neuere_dateien_werden_benannt(tmp_path):
    fremd = tmp_path / "fremd.zip"
    with zipfile.ZipFile(fremd, "w") as z:
        z.writestr("irgendwas.txt", "x")
    assert "kein Projekt" in _node("fehler", str(fremd))

    neuer = tmp_path / "neuer.zip"
    with zipfile.ZipFile(neuer, "w") as z:
        z.writestr("projekt.json", json.dumps({"format": "live-protocol-projekt",
                                               "version": 99, "transcript": []}))
    assert "neueren Fassung" in _node("fehler", str(neuer))

    kaputt = tmp_path / "kaputt.zip"
    kaputt.write_bytes(b"kein zip")
    assert "Keine ZIP-Datei" in _node("fehler", str(kaputt))
