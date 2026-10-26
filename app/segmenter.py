"""Segmentierung des PCM-Stroms an Sprechpausen.

Whisper kann kein Streaming, die Aufnahme muss also in Häppchen zerlegt
werden. Feste Schnittlängen zerschneiden Wörter mitten im Satz, deshalb wird
innerhalb eines Zielfensters die leiseste Stelle gesucht.

Außerdem: Whisper halluziniert auf Stille zuverlässig Text ("Untertitel von
..."), darum wird jedes Segment vor der Transkription auf Sprachanteil geprüft.
"""

from __future__ import annotations

import array
import math

FRAME_MS = 20
BYTES_PER_SAMPLE = 2


def _frame_rms(pcm: bytes, sample_rate: int, frame_ms: int = FRAME_MS) -> list[float]:
    """RMS je Frame (16-bit mono, little endian)."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)])
    frame_len = int(sample_rate * frame_ms / 1000)
    if frame_len <= 0:
        return []

    out: list[float] = []
    for start in range(0, len(samples) - frame_len + 1, frame_len):
        total = 0
        for i in range(start, start + frame_len):
            v = samples[i]
            total += v * v
        out.append(math.sqrt(total / frame_len))
    return out


def _silence_threshold(rms: list[float]) -> float:
    """Adaptive Schwelle: leise Stellen relativ zum Pegel der Aufnahme."""
    if not rms:
        return 0.0
    ordered = sorted(rms)
    median = ordered[len(ordered) // 2]
    # 20 % des Medians, aber nie unter einem absoluten Rauschboden.
    return max(median * 0.2, 60.0)


def speech_ratio(pcm: bytes, sample_rate: int) -> float:
    """Anteil der Frames mit Sprachpegel. 0.0 = vermutlich reine Stille."""
    rms = _frame_rms(pcm, sample_rate)
    if not rms:
        return 0.0
    threshold = _silence_threshold(rms)
    return sum(1 for v in rms if v > threshold) / len(rms)


def find_cut_point(
    pcm: bytes, sample_rate: int, min_seconds: float, max_seconds: float
) -> int | None:
    """Sucht die leiseste Stelle im Fenster [min, max] und gibt den Byte-Offset zurück.

    Rückgabe None bedeutet: noch nicht genug Material für ein Segment.
    Reicht der Puffer über max_seconds hinaus und ist keine Pause zu finden,
    wird hart bei max_seconds geschnitten - sonst wächst der Puffer endlos.
    """
    min_bytes = int(min_seconds * sample_rate) * BYTES_PER_SAMPLE
    max_bytes = int(max_seconds * sample_rate) * BYTES_PER_SAMPLE
    if len(pcm) < min_bytes:
        return None

    window = pcm[min_bytes : min(len(pcm), max_bytes)]
    rms = _frame_rms(window, sample_rate)
    if not rms:
        return min(len(pcm), max_bytes)

    threshold = _silence_threshold(rms)
    # Längste zusammenhängende Pause im Fenster suchen; in ihrer Mitte schneiden.
    best_start = best_len = 0
    run_start = None
    for i, value in enumerate(rms + [float("inf")]):
        if value <= threshold:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                run_len = i - run_start
                if run_len > best_len:
                    best_start, best_len = run_start, run_len
                run_start = None

    frame_bytes = int(sample_rate * FRAME_MS / 1000) * BYTES_PER_SAMPLE
    if best_len >= 2:  # mindestens 40 ms Pause
        centre = best_start + best_len // 2
        return min_bytes + centre * frame_bytes

    if len(pcm) >= max_bytes:
        return max_bytes
    return None
