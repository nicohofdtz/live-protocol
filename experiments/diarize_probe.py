"""Sprechererkennung: Was liefert Voxtral mit diarize?

Misst an einer Datei mit mehreren Sprechern und an einer mit einem:
Antwortformat, Kombinierbarkeit mit language/context_bias/Zeitstempeln,
Dauer, gefundene Sprecher. Die rohen Antworten landen in out/ (nicht
versioniert), die Befunde in BEFUNDE.md.

    uv run python experiments/diarize_probe.py <datei> [<datei> ...]
"""

from __future__ import annotations

import collections
import json
import pathlib
import sys
import time

import httpx

HIER = pathlib.Path(__file__).resolve().parent
URL = "https://api.eu.mistral.ai/v1/audio/transcriptions"


def schluessel() -> str:
    return next(l.split("=", 1)[1].strip().strip('"')
                for l in (HIER.parent / ".env").read_text().splitlines()
                if l.startswith("MISTRAL_API_KEY="))


def anfrage(datei: pathlib.Path, felder: dict) -> tuple[int, float, dict | str]:
    beginn = time.monotonic()
    r = httpx.post(URL, headers={"Authorization": f"Bearer {schluessel()}"}, timeout=900,
                   data={"model": "voxtral-mini-latest", **felder},
                   files={"file": (datei.name, datei.read_bytes(), "audio/mpeg")})
    dauer = round(time.monotonic() - beginn, 1)
    try:
        return r.status_code, dauer, r.json()
    except ValueError:
        return r.status_code, dauer, r.text[:300]


def auswerten(name: str, status: int, dauer: float, antwort) -> None:
    if status != 200:
        print(f"{name}: HTTP {status} nach {dauer} s: {str(antwort)[:300]}")
        return
    segmente = antwort.get("segments") or []
    beispiel = segmente[0] if segmente else {}
    sprecher = collections.Counter(s.get("speaker_id") or s.get("speaker") for s in segmente)
    zeit = collections.defaultdict(float)
    for s in segmente:
        zeit[s.get("speaker_id") or s.get("speaker")] += float(s.get("end", 0)) - float(s.get("start", 0))
    print(f"{name}: {dauer} s, Felder {sorted(antwort)}, {len(segmente)} Segmente, "
          f"Segmentfelder {sorted(beispiel)}")
    print(f"   Sprecher: {dict(sprecher)}")
    print(f"   Sprechzeit (s): {{{', '.join(f'{k}: {v:.0f}' for k, v in zeit.items())}}}")
    print(f"   usage: {antwort.get('usage')}")


def main(dateien: list[str]) -> None:
    varianten = {
        "diarize+ts": {"diarize": "true", "timestamp_granularities": "segment"},
        "diarize ohne ts": {"diarize": "true"},
        "diarize+ts+language": {"diarize": "true", "timestamp_granularities": "segment",
                                "language": "de"},
        "diarize+ts+context_bias": {"diarize": "true", "timestamp_granularities": "segment",
                                    "context_bias": ["Pritlove", "Freak Show"]},
    }
    for pfad in map(pathlib.Path, dateien):
        print(f"\n== {pfad.name}")
        for name, felder in varianten.items():
            status, dauer, antwort = anfrage(pfad, felder)
            auswerten(name, status, dauer, antwort)
            if status == 200:
                ziel = HIER / "out" / f"diarize-{pfad.stem}-{name.replace(' ', '_').replace('+', '_')}.json"
                ziel.write_text(json.dumps(antwort, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
