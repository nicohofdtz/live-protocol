"""Strom-Dienste: Satzbündelung und der Weg durch die Sitzung.

Die Verbindung zu Mistral wird nicht nachgebaut - geprüft wird, was hier
passiert, wenn wortweise Text ankommt.
"""

from __future__ import annotations

import array
import asyncio
import base64
import json
import math

import pytest

from app.config import SAMPLE_RATE, Engine, Settings, default_engine
from app.llm import Completion
from app.output_types import load_output_types
from app.session import LiveSession
from app.transcription import (
    MistralRealtimeTranscription,
    MistralTranscription,
    context_bias_items,
    SentenceAssembler,
    StreamingTranscriptionClient,
    create_from_engine,
)


# -- Satzbündelung ----------------------------------------------------------

def test_kurze_saetze_werden_gesammelt():
    a = SentenceAssembler(min_chars=40, max_chars=200)
    assert a.feed("Ja.") == [], "ein Wort ist noch kein Absatz"
    assert a.partial == "Ja."


def test_absatz_endet_am_satzzeichen():
    a = SentenceAssembler(min_chars=20, max_chars=200)
    fertig = a.feed("Wir bauen den Dienst ein und zwar heute.")
    assert fertig == ["Wir bauen den Dienst ein und zwar heute."]
    assert a.partial == ""


def test_notbremse_trennt_nie_mitten_im_wort():
    a = SentenceAssembler(min_chars=10, max_chars=30)
    text = "eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf"
    fertig = a.feed(text)
    assert fertig, "die Notbremse hat nicht ausgelöst"
    for absatz in fertig:
        assert not absatz.endswith(("ein", "zwe", "dre")), absatz
    # Nichts geht verloren.
    zusammen = " ".join(fertig + ([a.partial] if a.partial else []))
    assert zusammen.split() == text.split()


def test_flush_gibt_den_rest_heraus():
    a = SentenceAssembler(min_chars=100, max_chars=200)
    a.feed("Ein angefangener Satz ohne Ende")
    assert a.flush() == "Ein angefangener Satz ohne Ende"
    assert a.flush() == ""


# -- Client ------------------------------------------------------------------

def test_url_wird_zu_wss_mit_modell():
    client = MistralRealtimeTranscription("https://api.mistral.ai", "voxtral-x", "k")
    assert client.url == (
        "wss://api.mistral.ai/v1/audio/transcriptions/realtime?model=voxtral-x"
    )


def test_engine_baut_einen_strom_client():
    engine = Engine(id="rt", name="Realtime", type="mistral-realtime",
                    url="https://api.mistral.ai", model="voxtral-x",
                    api_key="k", local=False, realtime_delay_ms=240)
    client = create_from_engine(engine, vocabulary="wird hier ignoriert")
    assert isinstance(client, StreamingTranscriptionClient)
    assert client.target_delay_ms == 240


# -- Vorauswahl je Betriebsart ----------------------------------------------

def _engines() -> list[Engine]:
    return Settings(_env_file=None).transcription_engines


def test_desktop_waehlt_den_lokalen_dienst_vor():
    """Auf dem eigenen Rechner soll das Audio dort bleiben, bis jemand
    ausdrücklich etwas anderes wählt."""
    assert default_engine(_engines(), hosted=False) == "whisper-local"


def _mit_schluesseln(mistral=None, openrouter=None) -> list[Engine]:
    return [e for e in Settings(_env_file=None, mistral_api_key=mistral,
                                openrouter_api_key=openrouter).transcription_engines
            if not e.local]


def test_gehostet_waehlt_den_ersten_eintrag_mit_schluessel():
    """Mit nur einem Mistral-Schlüssel gewinnt Voxtral Realtime; ist ein
    OpenAI-kompatibler Anbieter mit Transkription da (hier über den alten
    OpenRouter-Schlüssel), gewinnt der billigere."""
    assert default_engine(_mit_schluesseln(mistral="m"), hosted=True) == "voxtral-realtime"
    assert default_engine(_mit_schluesseln(mistral="m", openrouter="o"),
                          hosted=True) == "openai-compatible"


def test_ohne_lokalen_dienst_gewinnt_der_erste_eintrag():
    assert default_engine(_mit_schluesseln(mistral="m"), hosted=False) == "voxtral-realtime"


def test_ohne_jeden_schluessel_gewinnt_der_erste_eintrag():
    engines = [e for e in _engines() if not e.local]
    assert default_engine(engines, hosted=True) == engines[0].id


def test_leere_liste_ergibt_leere_vorauswahl():
    assert default_engine([], hosted=False) == ""


# -- Vokabular ---------------------------------------------------------------

def test_begriffsliste_wird_in_einzelwoerter_zerlegt():
    """Voxtral nimmt je Eintrag genau ein Wort - Kommas und Leerzeichen
    quittiert die API mit HTTP 400."""
    assert context_bias_items("Kubernetes, Whisper, Freiwillige Feuerwehr") == [
        "Kubernetes", "Whisper", "Freiwillige", "Feuerwehr",
    ]
    assert context_bias_items("   ") == []
    assert len(context_bias_items(" ".join(str(i) for i in range(200)))) == 100


def test_voxtral_als_datei_schickt_context_bias():
    client = MistralTranscription("https://api.mistral.ai", "voxtral-mini-latest",
                                  "k", vocabulary="Voxtral, Ollama")
    felder = client.form_fields()
    assert felder["context_bias"] == ["Voxtral", "Ollama"]
    # prompt und response_format nimmt der Endpunkt zwar an, ignoriert sie aber.
    assert "prompt" not in felder and "response_format" not in felder


def test_ohne_vokabular_kein_leeres_feld():
    client = MistralTranscription("https://api.mistral.ai", "voxtral-mini-latest", "k")
    assert "context_bias" not in client.form_fields()


def test_nur_realtime_kennt_keine_begriffsliste():
    kann = {e.id: e.supports_vocabulary for e in _engines()}
    assert kann == {"voxtral-realtime": False, "whisper-local": True, "voxtral": True}


# -- Sitzung -----------------------------------------------------------------

def silence(seconds: float) -> bytes:
    return array.array("h", [0] * int(seconds * SAMPLE_RATE)).tobytes()


class StubStream(StreamingTranscriptionClient):
    """Liefert Wörter, sobald Audio hereinkommt - wie der echte Dienst."""

    def __init__(self, woerter: list[str]):
        self.woerter = woerter
        self.gesendete_bytes = 0

    async def stream(self, audio):
        i = 0
        async for chunk in audio:
            self.gesendete_bytes += len(chunk)
            while i < len(self.woerter):
                yield self.woerter[i]
                i += 1
        for wort in self.woerter[i:]:
            yield wort


class StubChat:
    def __init__(self):
        self.calls = 0

    async def complete(self, system: str, user: str, json_mode: bool = False,
                       temperature: float = 0.2) -> Completion:
        self.calls += 1
        text = ('{"ops":[{"op":"add_topic","title":"Thema","summary":"Zusammenfassung."}]}'
                if json_mode else "## Thema\n\nZusammenfassung.")
        return Completion(text=text, endpoint="stub", prompt_tokens=10, completion_tokens=5)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(sessions_dir=tmp_path / "sessions", trigger_chars=100,
                    trigger_seconds=9999)


def _session(settings, transcription, chat) -> LiveSession:
    return LiveSession(
        output_type=load_output_types()["meeting"], strategy_name="rewrite",
        transcription=transcription, chat=chat, settings=settings,
    )


async def test_wortstrom_wird_zu_transkript_und_protokoll(settings):
    satz = "Wir bauen Voxtral Realtime als zusätzlichen Dienst ein und machen ihn zum Standard. "
    woerter = [w + " " for w in (satz * 3).split()]
    transcription, chat = StubStream(woerter), StubChat()
    session = _session(settings, transcription, chat)
    assert session.streaming, "die Sitzung hätte den Strom-Weg wählen müssen"

    session.start()
    await session.add_audio(silence(2.0))
    await session.stop()

    assert transcription.gesendete_bytes > 0, "es wurde kein Audio weitergereicht"
    assert session.transcript, "Transkript ist leer"
    # Nichts geht verloren: alle Wörter stehen im Transkript.
    zusammen = " ".join(e["text"] for e in session.transcript)
    assert zusammen.split() == "".join(woerter).split()
    assert chat.calls >= 1, "es lief kein Protokolllauf"
    assert (session.dir / "transcript.md").read_text(encoding="utf-8")


async def test_stille_kostet_keinen_sprachanteil_test(settings):
    """Der Strom-Dienst erfindet auf Stille nichts - es wird nichts gefiltert."""
    transcription, chat = StubStream([]), StubChat()
    session = _session(settings, transcription, chat)
    session.start()
    await session.add_audio(silence(3.0))
    await session.stop()
    assert transcription.gesendete_bytes == 3 * 2 * SAMPLE_RATE
    assert session.transcript == []


async def test_pausierte_aufnahme_erreicht_den_dienst_nicht(settings):
    transcription, chat = StubStream([]), StubChat()
    session = _session(settings, transcription, chat)
    session.start()
    session.pause()
    await session.add_audio(silence(2.0))
    session.resume()
    await session.stop()
    assert transcription.gesendete_bytes == 0


# -- Verbindungsabriss ------------------------------------------------------------

class _FakeWs:
    """Eine Realtime-Verbindung, die auf Wunsch nach N Audioblöcken abreißt."""

    def __init__(self, reisst_nach: int | None):
        self.reisst_nach = reisst_nach
        self.bloecke: list[bytes] = []
        self.eingang: asyncio.Queue = asyncio.Queue()
        self.eingang.put_nowait(json.dumps({"type": "session.created"}))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def recv(self):
        return await self.eingang.get()

    async def send(self, raw: str):
        nachricht = json.loads(raw)
        if nachricht["type"] == "input_audio.append":
            self.bloecke.append(base64.b64decode(nachricht["audio"]))
            self.eingang.put_nowait(json.dumps(
                {"type": "transcription.text.delta", "text": f"w{len(self.bloecke)} "}))
            if self.reisst_nach is not None and len(self.bloecke) >= self.reisst_nach:
                # Wie im echten Fall: Die Empfangsseite bricht ab, während die
                # Sende-Aufgabe schon auf den nächsten Block der Aufnahme wartet.
                self.eingang.put_nowait(ConnectionError("Verbindung weg"))
        elif nachricht["type"] == "input_audio.end":
            self.eingang.put_nowait(json.dumps({"type": "transcription.done"}))

    def __aiter__(self):
        return self

    async def __anext__(self):
        eintrag = await self.eingang.get()
        if isinstance(eintrag, Exception):
            raise eintrag
        return eintrag


async def test_nach_einem_abriss_geht_die_aufnahme_weiter(monkeypatch):
    """Früher beendete der Abbruch der Sende-Aufgabe den Audio-Generator mit.
    Die neue Verbindung bekam eine leere Quelle, schickte sofort "Ende", und
    die Sitzung lief ohne Text weiter."""
    import websockets.asyncio.client as ws_client

    from app import transcription as tr

    verbindungen = [_FakeWs(reisst_nach=2), _FakeWs(reisst_nach=None)]
    monkeypatch.setattr(ws_client, "connect", lambda *a, **k: verbindungen.pop(0)
                        if verbindungen else pytest.fail("zu viele Verbindungen"))
    monkeypatch.setattr(tr, "_RECONNECT_DELAYS", (0.0, 0.0, 0.0, 0.0))
    erste, zweite = verbindungen

    async def aufnahme():
        for i in range(6):
            yield bytes([i]) * 10
            await asyncio.sleep(0.01)

    client = tr.MistralRealtimeTranscription("https://api.mistral.ai", "voxtral", "k")
    texte = [t async for t in client.stream(aufnahme())]

    assert len(erste.bloecke) == 2
    # Nichts geht verloren: Was während des Abrisses kam, wartet in der
    # Schlange und geht an die neue Verbindung.
    assert erste.bloecke + zweite.bloecke == [bytes([i]) * 10 for i in range(6)]
    assert texte[-1] == "w4 ", "nach dem Abriss kam kein Text mehr"
