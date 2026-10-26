"""PCM-Hilfen. Der Browser liefert bereits 16 kHz mono int16."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Verpackt rohes PCM in einen WAV-Container (für den Whisper-Endpoint)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return buffer.getvalue()
