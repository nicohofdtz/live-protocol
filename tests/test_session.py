"""Integrationstest der Sitzung mit Stubs für Whisper und LLM.

Prüft die Verkettung: PCM rein -> Segment geschnitten -> transkribiert ->
Protokolllauf ausgelöst -> auf Platte geschrieben.
"""

from __future__ import annotations

import array
import math

import pytest

from app.config import SAMPLE_RATE, Settings
from app.llm import Completion
from app.output_types import load_output_types
from app.session import LiveSession


def tone(seconds: float, amplitude: int = 8000) -> bytes:
    samples = array.array("h")
    for i in range(int(seconds * SAMPLE_RATE)):
        samples.append(int(amplitude * math.sin(2 * math.pi * 220.0 * i / SAMPLE_RATE)))
    return samples.tobytes()


def silence(seconds: float) -> bytes:
    return array.array("h", [0] * int(seconds * SAMPLE_RATE)).tobytes()


class StubTranscription:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, wav: bytes, filename: str = "chunk.wav") -> str:
        self.calls += 1
        return f"Transkribiertes Segment {self.calls}. " * 12


class StubChat:
    def __init__(self):
        self.calls = 0

    async def complete(self, system: str, user: str, json_mode: bool = False,
                       temperature: float = 0.2) -> Completion:
        self.calls += 1
        text = ('{"ops":[{"op":"add_topic","title":"Thema","summary":"Zusammenfassung."}]}'
                if json_mode else "## Thema\n\nZusammenfassung.")
        return Completion(text=text, endpoint="stub", prompt_tokens=100, completion_tokens=50)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(sessions_dir=tmp_path / "sessions", trigger_chars=200,
                    trigger_seconds=9999, segment_min_seconds=15, segment_max_seconds=35)


@pytest.mark.parametrize("strategy", ["rewrite", "delta"])
async def test_pcm_wird_zu_transkript_und_protokoll(settings, strategy):
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name=strategy,
        transcription=transcription,
        chat=chat,
        settings=settings,
    )
    session.start()
    # 18 s Ton, 2 s Pause, 18 s Ton -> zwei Segmente
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()

    assert transcription.calls >= 1, "es wurde kein Segment transkribiert"
    assert session.transcript, "Transkript ist leer"
    assert chat.calls >= 1, "es lief kein Protokolllauf"
    assert session.strategy.protocol, "Protokoll ist leer"

    assert (session.dir / "transcript.md").read_text(encoding="utf-8")
    assert (session.dir / "protocol.md").read_text(encoding="utf-8")
    assert (session.dir / "audio.raw").stat().st_size > 0
    if strategy == "delta":
        assert (session.dir / "state.json").exists()
        assert session.strategy.state.topics


async def test_stille_wird_nicht_transkribiert(settings):
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(silence(40.0))
    await session.stop()
    assert transcription.calls == 0, "Stille wurde an Whisper geschickt"
    assert session.transcript == []


async def test_abschlusslauf_glaettet_die_delta_struktur(settings):
    """Nach dem Stoppen läuft genau ein Politur-Durchgang über das Ergebnis."""
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="delta",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()

    # Der Politur-Lauf läuft ohne json_mode und liefert Markdown statt ops.
    assert session.strategy.protocol.startswith("## Thema")
    assert (session.dir / "protocol.md").read_text(encoding="utf-8").startswith("## Thema")


async def test_abschlusslauf_abschaltbar(settings):
    settings.final_pass = False
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="delta",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()
    # Ohne Politur bleibt die gerenderte Struktur stehen (Thema aus den ops).
    assert "## Thema" in session.strategy.protocol
    assert session.strategy.state.topics


async def test_pause_verwirft_audio(settings):
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    session.pause()
    await session.add_audio(tone(30.0))
    assert session._audio_seconds == 0.0, "pausiertes Audio wurde gepuffert"
    session.resume()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()
    assert session.transcript, "nach dem Fortsetzen kam nichts an"


async def test_feedback_landet_nicht_im_transkript(settings):
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.add_feedback("Punkt X kommt zu kurz.")
    await session.stop()

    assert session.feedback == ["Punkt X kommt zu kurz."]
    zusammen = " ".join(e["text"] for e in session.transcript)
    assert "Punkt X" not in zusammen, "Hinweis ist ins Transkript geraten"
    # Der Hinweis gilt dauerhaft, nicht nur einmal.
    assert "Punkt X kommt zu kurz." in session.strategy.feedback


async def test_export_enthaelt_protokoll_und_transkript(settings):
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.add_feedback("Bitte knapper.")
    await session.stop()

    markdown = session.export_markdown()
    assert "## Originaltranskript" in markdown
    assert "Transkribiertes Segment 1" in markdown
    assert "Bitte knapper." in markdown
    assert session.strategy.protocol.split("\n")[0] in markdown


async def test_feedback_nach_abschlusslauf_bleibt_poliert(settings):
    """Nach dem Abschlusslauf darf Feedback nicht auf die rohe Struktur zurueckfallen."""
    transcription, chat = StubTranscription(), StubChat()
    session = LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="delta",
        transcription=transcription, chat=chat, settings=settings,
    )
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()
    assert session.strategy.polished, "Abschlusslauf hat nicht stattgefunden"

    calls_before = chat.calls
    await session.add_feedback("Bitte ausbauen.")
    # Ueberarbeitung plus erneute Politur = zwei zusaetzliche Aufrufe.
    assert chat.calls == calls_before + 2
    assert session.strategy.polished


async def test_llm_auswahl_bindet_die_sitzung_an_einen_endpunkt():
    """Eine feste Auswahl darf nicht still auf einen anderen Endpunkt ausweichen."""
    from app.config import Endpoint
    from app.llm import ChatClient

    client = ChatClient([Endpoint(name="mistral", type="mistral",
                                  url="https://api.mistral.ai",
                                  model="mistral-small-latest", api_key="x", local=False)])
    assert [e.name for e in client.endpoints] == ["mistral"]


async def test_gehostet_bleibt_die_platte_leer(tmp_path):
    """Fremde Besprechungen sollen auf dem Server nicht liegen bleiben - auch
    nicht das rohe Audio, das früher parallel mitgeschrieben wurde."""
    settings = Settings(_env_file=None, deployment_mode="hosted",
                        sessions_dir=tmp_path, trigger_seconds=9999)
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    assert session.dir is None
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()

    assert session.transcript, "im Arbeitsspeicher muss die Sitzung trotzdem stehen"
    assert list(tmp_path.iterdir()) == [], "auf der Platte darf nichts liegen"


async def test_lokal_wird_weiter_gespeichert(settings):
    """Auf dem eigenen Rechner sind es die eigenen Dateien des Nutzers."""
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    assert session.dir is not None
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop()
    assert (session.dir / "transcript.md").is_file()


async def test_luecke_steht_im_transkript_und_loest_keinen_lauf_aus(settings):
    """Fehlende Minuten müssen sichtbar sein, sonst formuliert das Modell
    über das Loch hinweg. Ein Loch ist aber kein Inhalt: Es darf keinen
    Protokolllauf auslösen."""
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    session.start()
    vorher = session._chars_since_update
    session.note_gap(90)

    assert any(e.get("gap") for e in session.transcript)
    assert "90 s fehlen" in session.transcript[-1]["text"]
    assert session._chars_since_update == vorher, "eine Lücke ist kein Inhalt"


async def test_luecke_ohne_dauer_wird_verworfen(settings):
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    session.start()
    session.note_gap(0)
    assert session.transcript == []


async def test_terminrahmen_geht_in_den_prompt(settings):
    """Agenda und Beteiligte muessen im Systemtext ankommen - sonst hilft der
    Rahmen dem Protokoll nicht."""
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    session.set_context({"agenda": "1. Budget\n2. Termine", "participants": "BC, Anna"})

    text = session.strategy._guidance()
    assert "1. Budget" in text
    assert "BC, Anna" in text
    assert "keine Vorgabe" in text, "der Rahmen darf nicht als Auftrag gelesen werden"


async def test_leerer_terminrahmen_faellt_aus_dem_prompt(settings):
    session = LiveSession(
        output_type=load_output_types()["meeting"],
        strategy_name="rewrite",
        transcription=StubTranscription(),
        chat=StubChat(),
        settings=settings,
    )
    session.set_context({"agenda": "   ", "goal": ""})
    assert session.context == {}
    assert "Zum Termin ist vorab" not in session.strategy._guidance()


# -- Sitzungsende ---------------------------------------------------------------

def _meeting(settings, strategy="rewrite", transcription=None, chat=None) -> LiveSession:
    return LiveSession(
        output_type=load_output_types()["meeting"], strategy_name=strategy,
        transcription=transcription or StubTranscription(), chat=chat or StubChat(),
        settings=settings,
    )


async def test_doppelter_stopp_loest_genau_einen_abschlusslauf_aus(settings):
    """Der Browser schickt den Stopp über den WebSocket und per POST. Beide
    kommen gleichzeitig an - der Abschlusslauf darf trotzdem nur einmal laufen."""
    import asyncio

    session = _meeting(settings, strategy="delta")
    politur = 0
    original = session.strategy.polish

    async def zaehlend():
        nonlocal politur
        politur += 1
        return await original()

    session.strategy.polish = zaehlend
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await asyncio.gather(session.stop(), session.stop())
    await session.stop()  # auch ein später Nachzügler ändert nichts

    assert politur == 1
    assert session.stopped


async def test_protokolllauf_verliert_nach_einer_luecke_keinen_text(settings):
    """Früher schnitt der Lauf die letzten N Zeichen ab, ohne den Vermerk
    mitzuzählen - der Anfang des neuen Texts fiel dabei heraus."""
    session = _meeting(settings)
    gesehen: list[str] = []
    original = session.strategy.update

    async def merkend(segment, cycle):
        gesehen.append(segment)
        return await original(segment, cycle)

    session.strategy.update = merkend
    session._add_entry("Erster Absatz.", 0.0)
    await session._update_protocol()
    session.note_gap(30)
    session._add_entry("Anfang des neuen Absatzes und sein Rest.", 60.0)
    await session._update_protocol()

    assert gesehen[0] == "Erster Absatz."
    assert gesehen[1].endswith("Anfang des neuen Absatzes und sein Rest.")
    assert "30 s fehlen" in gesehen[1], "das Modell soll die Lücke sehen"


async def test_zeitgrenze_zaehlt_aufgenommenes_audio_nicht_die_wanduhr(tmp_path):
    settings = Settings(sessions_dir=tmp_path, deployment_mode="hosted",
                        max_session_minutes=1)
    session = _meeting(settings)
    session.start()
    session._started_at -= 3600  # eine Stunde Pause zählt nicht
    assert not session._over_time_limit()
    session._audio_seconds = 60.0
    assert session._over_time_limit()
    await session.stop()


async def test_zeitgrenze_beendet_die_sitzung_geordnet(tmp_path):
    """Wie ein Druck auf den Stopp-Knopf: Restpuffer, Abschlusslauf, Ereignis."""
    import asyncio

    settings = Settings(sessions_dir=tmp_path, deployment_mode="hosted",
                        max_session_minutes=1, trigger_chars=999_999,
                        trigger_seconds=9999, segment_min_seconds=15,
                        segment_max_seconds=35)
    chat = StubChat()
    session = _meeting(settings, chat=chat)
    ereignisse = session.subscribe()
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0) + tone(30.0))
    for _ in range(100):
        if session.stopped:
            break
        await asyncio.sleep(0.1)

    assert session.stopped, "die Sitzung hätte sich selbst beenden müssen"
    arten = []
    while not ereignisse.empty():
        arten.append(ereignisse.get_nowait()["type"])
    assert "limit_reached" in arten and arten[-1] == "stopped"
    assert chat.calls >= 1, "der Restpuffer hätte noch ins Protokoll gehen müssen"


async def test_abgestuerzter_worker_beendet_die_sitzung(settings):
    """Stirbt die Transkription, darf die Sitzung nicht als laufend stehen
    bleiben - sonst belegt sie den Platz, und niemand prüft die Zeitgrenze."""
    import asyncio

    class Kaputt(StubTranscription):
        async def transcribe(self, wav, filename="chunk.wav"):
            raise RuntimeError("unerwartet")

    session = _meeting(settings, transcription=Kaputt())
    ereignisse = session.subscribe()
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    for _ in range(100):
        if session.stopped:
            break
        await asyncio.sleep(0.1)

    assert session.stopped
    assert not session.running
    assert "unerwartet" in (session.failed or "")
    arten = []
    while not ereignisse.empty():
        arten.append(ereignisse.get_nowait()["type"])
    assert "error" in arten and arten[-1] == "stopped"


async def test_hinweis_laesst_keinen_codeblock_im_protokoll(settings):
    """Antwortet das Modell auf einen Hinweis mit ```markdown ... ```, stand
    der Zaun danach im Protokoll - anders als beim normalen Lauf."""
    class Umzaeunt(StubChat):
        async def complete(self, system, user, json_mode=False, temperature=0.2):
            antwort = await super().complete(system, user, json_mode, temperature)
            antwort.text = f"```markdown\n{antwort.text}\n```"
            return antwort

    session = _meeting(settings, chat=Umzaeunt())
    session.strategy.protocol = "## Alt"
    await session.add_feedback("Bitte kürzer.")
    assert not session.strategy.protocol.startswith("```")
    assert session.strategy.protocol.startswith("## Thema")


async def test_beendete_sitzung_laeuft_nicht_wieder_an(settings):
    """Meldet sich der Browser nach einem Abriss erst zurück, wenn die Sitzung
    schon zu ist, darf sie nicht neu starten."""
    session = _meeting(settings)
    session.start()
    await session.stop()
    session.start()
    assert not session.running


async def test_diktat_steht_als_luecke_im_transkript(settings):
    session = _meeting(settings)
    session.note_gap(20, "diktat")
    assert session.transcript[-1]["text"] == "[Diktat - etwa 20 s der Aufnahme fehlen]"
    assert session.transcript[-1]["gap"] is True


async def test_stopp_ohne_live_lauf_transkribiert_zu_ende(settings):
    """Kein letzter Lauf und keine Politur, aber das Transkript ist vollständig."""
    settings.trigger_chars = 100_000
    chat = StubChat()
    session = _meeting(settings, strategy="delta", chat=chat)
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.stop(live_pass=False)

    assert session.transcript
    assert chat.calls == 0
    assert session.stopped
    assert (session.dir / "transcript.md").exists()


async def test_stopp_ohne_live_lauf_bricht_laufenden_lauf_ab(settings):
    """Sonst wartete der Stopp doch wieder auf das Live-Modell."""
    import asyncio

    chat = StubChat()
    gestartet = asyncio.Event()

    async def haengt(*args, **kwargs):
        chat.calls += 1
        gestartet.set()
        await asyncio.Event().wait()

    chat.complete = haengt
    session = _meeting(settings, strategy="rewrite", chat=chat)
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await asyncio.wait_for(gestartet.wait(), timeout=5)

    await session.add_audio(tone(18.0) + silence(2.0))
    await asyncio.wait_for(session.stop(live_pass=False), timeout=5)
    assert chat.calls == 1
    assert session.stopped
    # Am Lauf hing die restliche Transkription: Sie muss trotzdem fertig werden.
    assert len(session.transcript) == 3


async def test_spaeter_stopp_mit_live_lauf_aendert_nichts(settings):
    """Zeigt der POST nach dem WebSocket-Stopp noch einmal hin: kein zweiter Abschluss."""
    settings.trigger_chars = 100_000
    chat = StubChat()
    session = _meeting(settings, strategy="rewrite", chat=chat)
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0))
    await session.stop(live_pass=False)
    await session.stop()
    assert chat.calls == 0


async def test_nachholen_arbeitet_den_abgebrochenen_lauf_ein(settings):
    """Sonst fehlte im Protokoll genau der Text, an dem der Stopp abbrach."""
    import asyncio

    chat = StubChat()
    original = chat.complete
    gestartet = asyncio.Event()
    eingaben = []

    async def haengt_einmal(system, user, **kwargs):
        eingaben.append(user)
        if len(eingaben) == 1:
            gestartet.set()
            await asyncio.Event().wait()
        return await original(system, user, **kwargs)

    chat.complete = haengt_einmal
    session = _meeting(settings, strategy="rewrite", chat=chat)
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await asyncio.wait_for(gestartet.wait(), timeout=5)
    await session.stop(live_pass=False)
    assert not session.strategy.protocol

    protokoll = await session.catch_up_live_pass()
    assert protokoll.startswith("## Thema")
    assert "Transkribiertes Segment 1" in eingaben[1]
    assert (session.dir / "protocol.md").read_text(encoding="utf-8") == protokoll

    await session.catch_up_live_pass()  # höchstens einmal
    assert len(eingaben) == 2


async def test_nachholen_nur_nach_ausgelassenem_lauf(settings):
    chat = StubChat()
    session = _meeting(settings, strategy="rewrite", chat=chat)
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0))
    await session.stop()
    aufrufe = chat.calls
    await session.catch_up_live_pass()
    assert chat.calls == aufrufe


async def test_delta_nimmt_operationen_als_nackte_liste(settings):
    """Ministral 8B liefert die Operationen ohne das Objekt drumherum."""
    class ListenChat(StubChat):
        async def complete(self, **kwargs):
            self.calls += 1
            return Completion(
                text='[{"op": "add_topic", "title": "Thema", "summary": "Inhalt"}]',
                endpoint="stub", prompt_tokens=1, completion_tokens=1)

    session = _meeting(settings, strategy="delta", chat=ListenChat())
    ergebnis = await session.strategy.update("Segment", 0)

    assert ergebnis.ops == 1
    assert session.strategy.state.invalid_ops == 0
    assert "Thema" in session.strategy.protocol
