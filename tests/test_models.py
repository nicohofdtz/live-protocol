"""Modell-Nachladen: Prüfsumme, Fortsetzen, Fehlerfälle - ohne Netz."""

from __future__ import annotations

import hashlib

import pytest

from app import models
from app.models import Model, ModelError


class StubResponse:
    def __init__(self, data: bytes, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def iter_bytes(self, size: int):
        for i in range(0, len(self._data), size):
            yield self._data[i : i + size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def modell(tmp_path, monkeypatch):
    inhalt = b"whisper-modell" * 500
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    m = Model(name="Test", filename="test.bin", url="https://example/test.bin",
              sha256=hashlib.sha256(inhalt).hexdigest(), size=len(inhalt))
    monkeypatch.setattr(Model, "path", property(lambda self: tmp_path / self.filename))
    return m, inhalt


def test_laedt_und_pruefte_summe(modell, monkeypatch):
    m, inhalt = modell
    monkeypatch.setattr(models.httpx, "stream", lambda *a, **k: StubResponse(inhalt))
    fortschritt = []
    pfad = models.ensure(m, on_progress=lambda a, b: fortschritt.append(a))
    assert pfad.read_bytes() == inhalt
    assert fortschritt and fortschritt[-1] == len(inhalt)
    assert not pfad.with_suffix(".bin.part").exists()


def test_falsche_pruefsumme_wird_verworfen(modell, monkeypatch):
    m, _ = modell
    monkeypatch.setattr(models.httpx, "stream", lambda *a, **k: StubResponse(b"etwas anderes"))
    with pytest.raises(ModelError, match="Prüfsumme"):
        models.ensure(m)
    # Kaputte Teildatei darf nicht liegen bleiben.
    assert not (m.path.with_suffix(".bin.part")).exists()
    assert not m.path.exists()


def test_setzt_abgebrochenen_download_fort(modell, monkeypatch):
    m, inhalt = modell
    teil = m.path.with_suffix(".bin.part")
    teil.write_bytes(inhalt[:1000])

    gesehen = {}

    def stream(method, url, headers=None, **kwargs):
        gesehen["range"] = (headers or {}).get("Range")
        return StubResponse(inhalt[1000:], status_code=206)

    monkeypatch.setattr(models.httpx, "stream", stream)
    pfad = models.ensure(m)
    assert gesehen["range"] == "bytes=1000-"
    assert pfad.read_bytes() == inhalt


def test_vorhandenes_modell_wird_nicht_erneut_geladen(modell, monkeypatch):
    m, inhalt = modell
    m.path.write_bytes(inhalt)

    def explodiere(*args, **kwargs):
        raise AssertionError("es wurde trotzdem geladen")

    monkeypatch.setattr(models.httpx, "stream", explodiere)
    assert models.ensure(m) == m.path
