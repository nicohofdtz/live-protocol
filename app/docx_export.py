"""Protokoll oder Transkript als Word-Datei.

Das Markdown kommt aus dem Browser - aus der Zusammenfassung oder aus dem
Transkript -, und es kommt genau das Markdown, das die Anwendung selbst
erzeugt: Überschriften, Absätze, Aufzählungen, Tabellen, fett und kursiv.
Mehr muss der Umsetzer nicht können; ein allgemeiner Markdown-Parser wäre
eine Abhängigkeit für Fälle, die hier nicht vorkommen.

Auf dem Server und nicht im Browser, damit auch die Desktop-Fassung ohne
Netz exportieren kann: python-docx steckt im Bündel.
"""

from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.shared import Pt

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*)")
_TABELLE = re.compile(r"^\s*\|.*\|\s*$")
_TRENNZEILE = re.compile(r"^:?-{2,}:?$")
_AUFZAEHLUNG = re.compile(r"^(\s*)[-*]\s+(.*)$")
_NUMMER = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
_UEBERSCHRIFT = re.compile(r"^(#{1,4})\s+(.*)$")


def _zeile(absatz, text: str) -> None:
    """Fett und kursiv als Formatierung, nicht als Sternchen."""
    for teil in _INLINE.split(text):
        if not teil:
            continue
        if teil.startswith("**") and teil.endswith("**"):
            absatz.add_run(teil[2:-2]).bold = True
        elif teil.startswith("*") and teil.endswith("*") and len(teil) > 2:
            absatz.add_run(teil[1:-1]).italic = True
        else:
            absatz.add_run(teil)


def _tabelle(dokument, zeilen: list[list[str]]) -> None:
    breite = max(len(z) for z in zeilen)
    tabelle = dokument.add_table(rows=len(zeilen), cols=breite)
    tabelle.style = "Table Grid"
    tabelle.alignment = WD_TABLE_ALIGNMENT.LEFT
    for r, zeile in enumerate(zeilen):
        for c in range(breite):
            zelle = tabelle.cell(r, c)
            zelle.text = ""
            _zeile(zelle.paragraphs[0], zeile[c] if c < len(zeile) else "")
            if r == 0:
                for run in zelle.paragraphs[0].runs:
                    run.bold = True
    dokument.add_paragraph()


def markdown_to_docx(markdown: str, title: str = "", meta: list[str] | None = None) -> bytes:
    dokument = Document()
    stil = dokument.styles["Normal"]
    stil.font.name = "Calibri"
    stil.font.size = Pt(11)

    zeilen = markdown.replace("\r\n", "\n").split("\n")
    # Bringt das Markdown eine eigene Hauptüberschrift mit, ist das der Titel.
    if zeilen and (m := re.match(r"^#\s+(.*)$", zeilen[0])):
        title = m.group(1).strip()
        zeilen = zeilen[1:]
    if title:
        dokument.add_heading(title, level=0)
    for eintrag in meta or []:
        absatz = dokument.add_paragraph()
        _zeile(absatz, eintrag)
        for run in absatz.runs:
            run.font.size = Pt(9)

    tabelle: list[list[str]] = []
    for roh in zeilen:
        zeile = roh.rstrip()
        if _TABELLE.match(zeile):
            zellen = [z.strip() for z in zeile.strip()[1:-1].split("|")]
            if not all(_TRENNZEILE.match(z) for z in zellen):
                tabelle.append(zellen)
            continue
        if tabelle:
            _tabelle(dokument, tabelle)
            tabelle = []
        if not zeile.strip() or zeile.strip() == "---":
            continue
        if m := _UEBERSCHRIFT.match(zeile):
            dokument.add_heading(m.group(2).strip(), level=min(len(m.group(1)), 4))
        elif m := _AUFZAEHLUNG.match(zeile):
            ebene = "List Bullet 2" if len(m.group(1)) >= 2 else "List Bullet"
            _zeile(dokument.add_paragraph(style=ebene), m.group(2))
        elif m := _NUMMER.match(zeile):
            _zeile(dokument.add_paragraph(style="List Number"), m.group(2))
        else:
            _zeile(dokument.add_paragraph(), zeile.strip())
    if tabelle:
        _tabelle(dokument, tabelle)

    # Maschinenlesbar kennzeichnen, dass der Text mit KI entstand (Art. 50
    # Abs. 2 KI-Verordnung) - sichtbar steht es ohnehin in der Kopfzeile.
    eigenschaften = dokument.core_properties
    eigenschaften.author = "Live-Protokoll"
    eigenschaften.comments = "Mit KI erstellt (Transkription und Zusammenfassung durch " \
                             "Sprachmodelle). " + " · ".join(meta or [])[:900]
    eigenschaften.keywords = "KI-generiert"

    puffer = io.BytesIO()
    dokument.save(puffer)
    return puffer.getvalue()
