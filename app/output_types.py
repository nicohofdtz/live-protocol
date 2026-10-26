"""Ausgabetypen: was inhaltlich erfasst werden soll (Modus), als Markdown-Datei.

Bewusst getrennt von der Strategie (wie fortgeschrieben wird): der Modus sagt
"Meeting" oder "Brainstorming", die Strategie sagt "neu schreiben" oder
"Deltas". Beides ist frei kombinierbar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)---\s*\n?(.*)$", re.DOTALL)
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "output_types"


class OutputTypeError(Exception):
    pass


@dataclass(frozen=True)
class OutputType:
    id: str
    name: str
    # Ein bis zwei Wörter für Schalter und Überschrift. Der lange Name erklärt,
    # was der Modus tut; auf einer Schaltfläche steht er im Weg.
    short_name: str
    description: str
    default_strategy: str
    guidance: str
    # Bausteine der Zusammenfassung nach dem Termin: je ein Kästchen mit
    # Anweisung. Je Modus verschieden, deshalb hier und nicht im Code.
    summary_blocks: tuple["SummaryBlock", ...] = field(default=())


@dataclass(frozen=True)
class SummaryBlock:
    id: str
    label: str
    instruction: str
    default: bool = False


def _parse(path: Path) -> OutputType:
    match = _FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
    if not match:
        raise OutputTypeError(f"{path.name}: kein gültiges YAML-Frontmatter")
    meta = yaml.safe_load(match.group(1)) or {}
    for field in ("id", "name", "description", "default_strategy"):
        if field not in meta:
            raise OutputTypeError(f"{path.name}: Feld '{field}' fehlt")
    return OutputType(
        id=str(meta["id"]),
        name=str(meta["name"]),
        short_name=str(meta.get("short_name") or meta["name"]),
        description=str(meta["description"]),
        default_strategy=str(meta["default_strategy"]),
        guidance=match.group(2).strip(),
        summary_blocks=tuple(
            SummaryBlock(id=str(b["id"]), label=str(b["label"]),
                         instruction=str(b["instruction"]).strip(),
                         default=bool(b.get("default", False)))
            for b in meta.get("summary_blocks") or ()
        ),
    )


def load_output_types(directory: Path | None = None) -> dict[str, OutputType]:
    result: dict[str, OutputType] = {}
    for path in sorted((directory or DEFAULT_DIR).glob("*.md")):
        # macOS legt beim Packen AppleDouble-Dateien "._name.md" an. Die sind
        # binaer und wuerden hier als Ausgabetyp durchgehen - beobachtet beim
        # Ausrollen ueber ein tar vom Mac.
        if path.name.startswith("._"):
            continue
        item = _parse(path)
        if item.id in result:
            raise OutputTypeError(f"Doppelte id '{item.id}' in {path.name}")
        result[item.id] = item
    return result
