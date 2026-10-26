"""Einstellungen je Benutzer, auf dem Server abgelegt.

Warum nicht im Browser: Wer den Dienst am Telefon und am Rechner benutzt, will
seine Auswahl an beiden Stellen wiederfinden - und `localStorage` ist nach
einem privaten Fenster oder einem geleerten Speicher weg. Es sind ausdrücklich
**Einstellungen**, keine Sitzungsinhalte; die bleiben im Arbeitsspeicher.

Gespeichert wird nur, was hier steht. Ein Feld, das die Oberfläche mitschickt
und der Server nicht kennt, fällt heraus, statt in die Datei zu wandern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

FELDER = ("engine", "output_type", "strategy", "llm_endpoint", "vocabulary",
          "trigger_seconds", "trigger_chars", "live_protocol",
          "summary_model", "summary_detail", "summary_blocks",
          "speakers_after_stop", "upload_diarize",
          "glossaries", "active_glossaries", "glossary_target", "news_seen")

# Glossare sind größer als eine Einstellung: bis zu 20 Listen mit je einigen
# hundert Begriffen.
GROSSE_FELDER = {"glossaries": 60_000}

# Der Benutzername wird zum Dateinamen - alles andere als diese Zeichen wäre
# ein Pfad, kein Name.
_SAUBER = re.compile(r"[^A-Za-z0-9_.-]")


def _datei(verzeichnis: Path, benutzer: str) -> Path:
    name = _SAUBER.sub("_", benutzer) or "unbekannt"
    return verzeichnis / f"{name}.json"


def _wert(wert: Any, grenze: int = 2000) -> Any:
    """Zahlen und Wahrheitswerte bleiben, was sie sind - ein Schalter käme
    sonst als Text "False" zurück, und der ist in JavaScript wahr. Alles
    andere wird zu begrenztem Text."""
    if wert is None:
        return ""
    if isinstance(wert, (bool, int, float)):
        return wert
    if isinstance(wert, (list, dict)):
        # Etwa die angekreuzten Bausteine je Modus. Begrenzt wie Text, damit
        # die Datei kein Eimer für beliebig große Strukturen wird.
        return wert if len(json.dumps(wert, ensure_ascii=False)) <= grenze else ""
    return str(wert)[:grenze]


def load(verzeichnis: Path, benutzer: str) -> dict[str, Any]:
    try:
        daten = json.loads(_datei(verzeichnis, benutzer).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in daten.items() if k in FELDER} if isinstance(daten, dict) else {}


def save(verzeichnis: Path, benutzer: str, werte: dict[str, Any]) -> dict[str, Any]:
    """Bekannte Felder übernehmen, den Rest verwerfen, Ergebnis zurückgeben."""
    stand = load(verzeichnis, benutzer)
    for feld in FELDER:
        if feld in werte:
            stand[feld] = _wert(werte[feld], GROSSE_FELDER.get(feld, 2000))
    verzeichnis.mkdir(parents=True, exist_ok=True)
    _datei(verzeichnis, benutzer).write_text(
        json.dumps(stand, ensure_ascii=False, indent=2), encoding="utf-8")
    return stand


def delete(verzeichnis: Path, benutzer: str) -> None:
    """Die Einstellungen dieses Benutzers entfernen (Art. 17 DSGVO)."""
    _datei(verzeichnis, benutzer).unlink(missing_ok=True)
