"""Wie lange braucht eine einmalige Zusammenfassung, und fasst das Modell sie?

Für die Nachbearbeitung: Die Zusammenfassung am Ende sieht das
ganze Transkript auf einmal. Gemessen wird je Länge die Dauer, die Tokens und
ob Anfang, Mitte und Ende im Ergebnis ankommen. Dafür stecken drei
erfundene, eindeutige Aussagen im Text - je eine am Anfang, in der Mitte und
am Ende. Fehlt eine davon im Ergebnis, hat das Modell den Teil verloren.

Material: das Talkshow-Transkript aus out/full.json (rund 3.000 Wörter),
für größere Längen mehrfach aneinandergehängt. Der Inhalt wiederholt sich
dadurch - für Dauer und Kontext spielt das keine Rolle, für die Qualität
schon. Qualität wird hier nicht beurteilt.

    uv run python experiments/summary_length.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import httpx

HIER = pathlib.Path(__file__).resolve().parent
MARKEN = {
    "anfang": ("Frau Obermaier übernimmt zu Beginn die Moderation der Runde.", "Obermaier"),
    "mitte": ("An dieser Stelle wird ein Budget von 47.300 Euro für das Pilotprojekt freigegeben.", "47.300"),
    "ende": ("Zum Schluss beschließt die Runde ein Folgetreffen am 14. Oktober in Kassel.", "Kassel"),
}
SYSTEM = ("Du schreibst aus einem Besprechungstranskript ein sachliches Protokoll auf "
          "Deutsch in Markdown: Themen, Ergebnisse, Entscheidungen, Aufgaben. "
          "Nur was gesagt wurde, nichts hinzuerfinden.")


def material(woerter: int) -> str:
    segs = json.loads((HIER / "out" / "full.json").read_text())["transcription"]
    basis = " ".join(s["text"].strip() for s in segs).split()
    text = []
    while len(text) < woerter:
        text += basis
    text = text[:woerter]
    mitte = len(text) // 2
    return " ".join([MARKEN["anfang"][0]] + text[:mitte] + [MARKEN["mitte"][0]]
                    + text[mitte:] + [MARKEN["ende"][0]])


def main() -> None:
    key = os.environ.get("MISTRAL_API_KEY") or next(
        l.split("=", 1)[1].strip().strip('"')
        for l in (HIER.parent / ".env").read_text().splitlines()
        if l.startswith("MISTRAL_API_KEY="))
    base = "https://api.eu.mistral.ai"
    ergebnisse = []
    for effort in ("low", "high"):
        for woerter in (1_000, 5_000, 10_000, 30_000):
            t = time.monotonic()
            r = httpx.post(f"{base}/v1/chat/completions", timeout=900,
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": "zai-glm-5-3", "reasoning_effort": effort,
                                 "messages": [{"role": "system", "content": SYSTEM},
                                              {"role": "user", "content": material(woerter)}]})
            dauer = time.monotonic() - t
            if r.status_code != 200:
                zeile = {"effort": effort, "woerter": woerter, "status": r.status_code,
                         "fehler": r.text[:300], "dauer_s": round(dauer, 1)}
            else:
                j = r.json()
                c = j["choices"][0]["message"]["content"]
                text = c if isinstance(c, str) else "".join(
                    x.get("text", "") for x in c if x.get("type") == "text")
                zeile = {"effort": effort, "woerter": woerter, "status": 200,
                         "dauer_s": round(dauer, 1), **j["usage"],
                         "gefunden": {k: v[1] in text for k, v in MARKEN.items()},
                         "zeichen": len(text)}
            print(json.dumps(zeile, ensure_ascii=False), flush=True)
            ergebnisse.append(zeile)
    (HIER / "out" / "summary-length.json").write_text(
        json.dumps(ergebnisse, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
