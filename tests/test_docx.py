"""Word-Export: aus dem Markdown der Anwendung wird eine lesbare .docx."""

from __future__ import annotations

import io

from docx import Document

from app.docx_export import markdown_to_docx
from tests.test_api import hosted  # noqa: F401

PROTOKOLL = """# Sommerfest 2027

## Kurzfassung
Es ging um **Budget** und *Termine*.

- Kuchen vom Bäcker
  - für hundert Leute
1. Erster Punkt

## Aufgaben

| Was | Wer | Bis wann |
|---|---|---|
| Hüpfburg | Martina | 15. Juni |
"""


def test_markdown_wird_zu_word():
    dokument = Document(io.BytesIO(markdown_to_docx(PROTOKOLL, meta=["Aufgenommen am 23.09."])))
    absaetze = [(p.style.name, p.text) for p in dokument.paragraphs if p.text]
    assert absaetze[0] == ("Title", "Sommerfest 2027"), "die eigene Überschrift ist der Titel"
    assert ("Heading 2", "Kurzfassung") in absaetze
    assert ("List Bullet", "Kuchen vom Bäcker") in absaetze
    assert ("List Bullet 2", "für hundert Leute") in absaetze
    assert ("List Number", "Erster Punkt") in absaetze
    fett = [r.text for p in dokument.paragraphs for r in p.runs if r.bold]
    assert "Budget" in fett, "Sternchen werden zu Formatierung"
    assert not any("**" in text for _, text in absaetze)
    tabelle = dokument.tables[0]
    assert [c.text for c in tabelle.rows[1].cells] == ["Hüpfburg", "Martina", "15. Juni"]


def test_endpunkt_liefert_eine_docx(hosted):  # noqa: F811
    antwort = hosted.post("/api/export/docx", json={
        "markdown": PROTOKOLL, "filename": "2026-09-23-protokoll", "meta": ["x"]})
    assert antwort.status_code == 200
    assert "2026-09-23-protokoll.docx" in antwort.headers["content-disposition"]
    assert Document(io.BytesIO(antwort.content)).tables


def test_leerer_export_wird_abgewiesen(hosted):  # noqa: F811
    assert hosted.post("/api/export/docx",
                       json={"markdown": " "}).status_code == 400
