"""Nachladen des Whisper-Modells beim ersten Start.

Die 574 MB stecken bewusst nicht im Installer: das Modell ist
plattformunabhängig, und drei Downloads mit demselben Inhalt auszuliefern
kostet nur Bandbreite. Geprüft wird über die Prüfsumme, nicht über eine
festgenagelte Revision - so fällt auch auf, wenn sich die Datei bei
Hugging Face ändert.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.paths import models_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Model:
    name: str
    filename: str
    url: str
    sha256: str
    size: int

    @property
    def path(self) -> Path:
        return models_dir() / self.filename


DEFAULT = Model(
    name="Whisper large-v3-turbo (q5_0)",
    filename="ggml-large-v3-turbo-q5_0.bin",
    url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"
        "ggml-large-v3-turbo-q5_0.bin",
    sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    size=574_041_195,
)


class ModelError(Exception):
    pass


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def is_present(model: Model = DEFAULT) -> bool:
    return model.path.exists() and model.path.stat().st_size == model.size


def ensure(
    model: Model = DEFAULT,
    on_progress: Callable[[int, int], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    """Lädt das Modell, falls es fehlt, und gibt den Pfad zurück.

    Ein abgebrochener Download wird fortgesetzt: die Teildatei bleibt liegen
    und wird per Range-Anfrage weitergeführt.
    """
    if is_present(model):
        return model.path

    target = model.path
    partial = target.with_suffix(target.suffix + ".part")
    done = partial.stat().st_size if partial.exists() else 0
    if done > model.size:          # kaputter Rest, neu anfangen
        partial.unlink()
        done = 0

    headers = {"Range": f"bytes={done}-"} if done else {}
    mode = "ab" if done else "wb"
    logger.info("Lade %s (%.0f MB)%s", model.name, model.size / 1e6,
                f", setze bei {done / 1e6:.0f} MB fort" if done else "")

    try:
        with httpx.stream("GET", model.url, headers=headers,
                          follow_redirects=True, timeout=timeout) as response:
            if done and response.status_code == 200:
                # Server ignoriert Range - von vorn schreiben.
                mode, done = "wb", 0
            elif done and response.status_code != 206:
                raise ModelError(f"Fortsetzen fehlgeschlagen: HTTP {response.status_code}")
            elif not done and response.status_code != 200:
                raise ModelError(f"Download fehlgeschlagen: HTTP {response.status_code}")

            with partial.open(mode) as handle:
                for chunk in response.iter_bytes(1024 * 256):
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, model.size)
    except httpx.HTTPError as exc:
        raise ModelError(f"Download fehlgeschlagen: {exc}") from exc

    actual = _digest(partial)
    if actual != model.sha256:
        partial.unlink(missing_ok=True)
        raise ModelError(
            "Prüfsumme stimmt nicht. Die Datei bei Hugging Face hat sich "
            f"geändert oder der Download war unvollständig.\n"
            f"  erwartet: {model.sha256}\n  erhalten: {actual}"
        )

    partial.replace(target)
    logger.info("Modell liegt unter %s", target)
    return target
