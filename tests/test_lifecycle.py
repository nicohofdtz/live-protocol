"""Lebensdauer von Sitzungen im Server: aufräumen und verwaiste beenden."""

from __future__ import annotations

import time

import pytest

from app import main
from app.config import Settings
from app.output_types import load_output_types
from app.session import LiveSession
from tests.test_session import StubChat, StubTranscription


@pytest.fixture
def leer():
    main.sessions.clear()
    main.owners.clear()
    yield
    main.sessions.clear()
    main.owners.clear()


def _anlegen(tmp_path) -> LiveSession:
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=StubTranscription(), chat=StubChat(),
        settings=Settings(sessions_dir=tmp_path),
    )
    main.sessions[session.id] = session
    main.owners[session.id] = "anna"
    return session


async def test_beendete_sitzung_fliegt_nach_der_frist(leer, tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "session_ttl_minutes", 30.0)
    session = _anlegen(tmp_path)
    session.start()
    await session.stop()

    assert main.reap_sessions(time.monotonic() + 29 * 60) == []
    assert main.reap_sessions(time.monotonic() + 31 * 60) == [session.id]
    assert session.id not in main.sessions and session.id not in main.owners


async def test_laufende_sitzung_bleibt_stehen(leer, tmp_path):
    session = _anlegen(tmp_path)
    session.start()
    assert main.reap_sessions(time.monotonic() + 24 * 3600) == []
    await session.stop()


async def test_die_frist_zaehlt_ab_der_letzten_nutzung(leer, tmp_path, monkeypatch):
    """Wer nach dem Termin noch exportiert, verlängert die Frist."""
    monkeypatch.setattr(main.settings, "session_ttl_minutes", 30.0)
    session = _anlegen(tmp_path)
    session.start()
    await session.stop()
    session.last_activity -= 25 * 60
    session.touch()
    assert main.reap_sessions(time.monotonic() + 20 * 60) == []


async def test_verwaiste_sitzung_beendet_sich(leer, tmp_path):
    session = _anlegen(tmp_path)
    session.start()
    await main.stop_if_orphaned(session, grace=0)
    assert session.stopped


async def test_neu_verbundene_sitzung_laeuft_weiter(leer, tmp_path):
    session = _anlegen(tmp_path)
    session.start()
    queue = session.subscribe()  # der Browser hat sich wieder verbunden
    await main.stop_if_orphaned(session, grace=0)
    assert session.running
    session.unsubscribe(queue)
    await session.stop()
