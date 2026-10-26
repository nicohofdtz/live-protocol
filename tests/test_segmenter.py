"""Segmentierung: Schnitt an Pausen, Stille-Erkennung."""

from __future__ import annotations

import array
import math

from app.segmenter import find_cut_point, speech_ratio

SR = 16000


def tone(seconds: float, amplitude: int = 8000, freq: float = 220.0) -> bytes:
    samples = array.array("h")
    for i in range(int(seconds * SR)):
        samples.append(int(amplitude * math.sin(2 * math.pi * freq * i / SR)))
    return samples.tobytes()


def silence(seconds: float) -> bytes:
    return array.array("h", [0] * int(seconds * SR)).tobytes()


def test_stille_hat_keinen_sprachanteil():
    assert speech_ratio(silence(2.0), SR) == 0.0


def test_ton_hat_sprachanteil():
    assert speech_ratio(tone(2.0), SR) > 0.9


def test_kein_schnitt_unterhalb_des_mindestfensters():
    assert find_cut_point(tone(5.0), SR, min_seconds=15, max_seconds=35) is None


def test_schnitt_landet_in_der_pause():
    # 18 s Ton, 2 s Pause, 10 s Ton -> Schnitt soll in die Pause fallen.
    pcm = tone(18.0) + silence(2.0) + tone(10.0)
    cut = find_cut_point(pcm, SR, min_seconds=15, max_seconds=35)
    assert cut is not None
    cut_seconds = cut / 2 / SR
    assert 18.0 < cut_seconds < 20.0


def test_harter_schnitt_wenn_keine_pause_da_ist():
    pcm = tone(40.0)
    cut = find_cut_point(pcm, SR, min_seconds=15, max_seconds=35)
    assert cut == int(35 * SR) * 2


def test_schnitt_bleibt_unter_der_30_sekunden_grenze():
    """Whisper arbeitet immer mit einem 30-s-Fenster; alles darueber oeffnet ein
    zweites, fast leeres. Gemessen: 29 s kosten 3,09 s, 31 s kosten 4,87 s."""
    from app.config import Settings

    # Bewusst die Feld-Vorgaben, nicht Settings(): eine vorhandene .env wuerde
    # sie ueberschreiben und der Test pruefte dann die lokale Umgebung.
    felder = Settings.model_fields
    min_s = felder["segment_min_seconds"].default
    max_s = felder["segment_max_seconds"].default
    assert max_s <= 29, "Maximum reicht ueber die Fenstergrenze"
    assert min_s < max_s

    # Ohne Pause wird hart am Maximum geschnitten - das darf die Grenze nicht reissen.
    pcm = tone(60.0)
    cut = find_cut_point(pcm, SR, min_s, max_s)
    assert cut is not None
    assert cut / 2 / SR <= 30.0
