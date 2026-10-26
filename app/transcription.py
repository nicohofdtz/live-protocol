"""Transkriptions-Backends hinter einer gemeinsamen Schnittstelle.

`type` unterscheidet den API-Dialekt, nicht den Ort - ein lokaler Server ist
kein Sonderfall, sondern nur eine andere URL.

Zwei Bauarten, die sich grundlegend unterscheiden:

* **Datei-Dienste** (`TranscriptionClient`) bekommen ein fertiges Segment
  geschickt und antworten mit dem Text dazu. Wer schneidet, ist unsere Sache.
* **Strom-Dienste** (`StreamingTranscriptionClient`) halten eine Verbindung
  offen, bekommen Audio laufend und liefern Text zurück, während gesprochen
  wird. Die Segmentierung entfällt - der Dienst hört selbst, wo ein Wort
  endet.

Achtung: whisper.cpp's whisper-server bedient /inference, NICHT den
OpenAI-Pfad /v1/audio/transcriptions. Deshalb ein eigener Dialekt.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import suppress
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
_WS_RE = re.compile(r"[ \t]+")
_BIAS_SPLIT = re.compile(r"[,\s]+")


def context_bias_items(vocabulary: str, limit: int = 100) -> list[str]:
    """Vokabularliste in Einzelbegriffe für Voxtral zerlegen.

    Die API nimmt je Eintrag genau ein Wort: Kommas, Leerzeichen und leere
    Einträge quittiert sie mit HTTP 400 (Code 3051). Ein mehrwortiger Begriff
    wird deshalb in seine Wörter zerlegt - jedes einzelne wirkt weiterhin auf
    die Schreibweise. Mehr als 100 Begriffe nimmt die API nicht an.
    """
    return [begriff for begriff in _BIAS_SPLIT.split(vocabulary.strip()) if begriff][:limit]


class TranscriptionError(Exception):
    pass


def normalise(text: str) -> str:
    """Zeilenumbrüche von whisper.cpp auflösen, ohne Wörter zu zerreißen.

    whisper-server bricht bei rund 60 Zeichen um und trennt dabei an
    Token- statt an Wortgrenzen. Ein naives Ersetzen von "\n" durch ein
    Leerzeichen macht daraus "stim me" und "Whis per".

    Verlässliches Signal: eine normale Fortsetzungszeile beginnt mit einem
    Leerzeichen, eine mitten im Wort getrennte nicht. Entsprechend wird
    zusammengefügt.
    """
    lines = text.split("\n")
    if not lines:
        return ""
    out = lines[0].strip()
    for line in lines[1:]:
        if not line:
            continue
        if line[0] in " \t":
            out += " " + line.strip()
        else:
            out += line.strip()  # mitten im Wort getrennt: direkt ankleben
    return _WS_RE.sub(" ", out).strip()


class TranscriptionClient(ABC):
    @abstractmethod
    async def transcribe(self, wav: bytes, filename: str = "chunk.wav") -> str: ...

    #: Ob der Dienst Sprecher unterscheiden kann.
    diarize = False

    async def transcribe_file(self, data: bytes, filename: str, content_type: str,
                              diarize: bool = False) -> list[dict]:
        """Eine ganze Datei auf einmal, mit Zeitstempeln je Segment.

        Liefert [{"start", "end", "text"}], mit `diarize` zusätzlich
        "speaker". Nicht jeder Dienst kann das - der lokale whisper-server
        nimmt nur WAV und bleibt beim alten Weg."""
        raise TranscriptionError("Dieser Dienst nimmt keine ganze Datei an.")


class StreamingTranscriptionClient(ABC):
    """Dienst mit stehender Verbindung.

    `audio` ist die laufende Aufnahme als Strom von PCM-Blöcken; sie endet,
    wenn die Aufnahme endet. Zurück kommen Textstücke in der Reihenfolge, in
    der sie gesprochen wurden - typischerweise einzelne Wörter.
    """

    @abstractmethod
    def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]: ...


_SENTENCE_END = ".!?…"


def paragraphs(segments: list[dict], min_chars: int = 120,
               max_chars: int = 600) -> list[dict]:
    """Segmente einer ganzen Datei zu Absätzen bündeln, wie im Live-Transkript.

    Ein Dienst liefert Satzstücke von wenigen Sekunden - als je eine Zeile
    wären es für eine Stunde über 500. Jeder Absatz trägt die Startzeit seines
    ersten Segments. Mit Sprechern endet ein Absatz außerdem bei jedem
    Sprecherwechsel, und er trägt den Sprecher: Ein Einwurf wie „Ja." bleibt
    dann eine eigene Zeile, statt im Absatz des anderen zu verschwinden."""
    absaetze: list[dict] = []
    text, start, sprecher = "", None, None

    def abschliessen() -> None:
        nonlocal text, start
        if text:
            absatz = {"text": text, "seconds": round(start or 0.0, 1)}
            if sprecher is not None:
                absatz["speaker"] = sprecher
            absaetze.append(absatz)
        text, start = "", None

    for segment in segments:
        if segment.get("speaker") != sprecher:
            abschliessen()
            sprecher = segment.get("speaker")
        if start is None:
            start = segment["start"]
        text = f"{text} {segment['text']}".strip()
        if (len(text) >= min_chars and text[-1] in _SENTENCE_END) or len(text) >= max_chars:
            abschliessen()
    abschliessen()
    return absaetze


class SentenceAssembler:
    """Wortweise Textstücke zu lesbaren Absätzen bündeln.

    Ein Absatz je Wort wäre weder lesbar noch für die Protokollführung
    brauchbar - das Sprachmodell bekäme Kleinstkram statt Aussagen. Gebündelt
    wird deshalb bis zum Satzende, und erst ab einer Mindestlänge: „Ja." ist
    ein vollständiger Satz, aber kein Absatz.
    """

    def __init__(self, min_chars: int = 120, max_chars: int = 600):
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buffer = ""

    @property
    def partial(self) -> str:
        """Was gerade angesammelt, aber noch nicht ausgegeben ist."""
        return self._buffer.strip()

    def feed(self, text: str) -> list[str]:
        self._buffer += text
        fertig = []
        while (satz := self._take()) is not None:
            fertig.append(satz)
        return fertig

    def flush(self) -> str:
        """Rest ausgeben, unabhängig von Länge und Satzzeichen (Aufnahmeende)."""
        rest = self._buffer.strip()
        self._buffer = ""
        return rest

    def _take(self) -> str | None:
        buffer = self._buffer.strip()
        if len(buffer) < self.min_chars:
            return None
        if buffer[-1] in _SENTENCE_END:
            self._buffer = ""
            return buffer
        if len(buffer) < self.max_chars:
            return None
        # Notbremse: es kommt kein Satzende. Am letzten Satzzeichen trennen,
        # sonst an der letzten Wortgrenze - nie mitten im Wort.
        cut = max(buffer.rfind(zeichen, self.min_chars) for zeichen in _SENTENCE_END)
        if cut < 0:
            cut = buffer.rfind(" ", self.min_chars)
        if cut < 0:
            cut = len(buffer) - 1
        self._buffer = buffer[cut + 1:]
        return buffer[: cut + 1].strip()


class _HTTPTranscription(TranscriptionClient):
    path = "/v1/audio/transcriptions"
    send_model = True

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 language: str = "de", vocabulary: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.language = language
        # Eigennamen, die das Modell sonst verfremdet - wird als Prompt
        # mitgegeben und beeinflusst die Schreibweise.
        self.vocabulary = vocabulary

    def form_fields(self) -> dict[str, str | list[str]]:
        """Formularfelder des Dialekts. Fremde Felder quittieren manche APIs
        mit einem Fehler, deshalb schickt jeder Dialekt nur seine eigenen."""
        data = {"response_format": "json", "language": self.language}
        if self.vocabulary:
            data["prompt"] = self.vocabulary
        if self.send_model:
            data["model"] = self.model
        return data

    async def transcribe(self, wav: bytes, filename: str = "chunk.wav") -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self.form_fields()

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                self.base_url + self.path,
                headers=headers,
                data=data,
                files={"file": (filename, wav, "audio/wav")},
            )
        if response.status_code >= 400:
            raise TranscriptionError(f"HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        text = payload.get("text")
        if text is None:
            raise TranscriptionError(f"Antwort ohne 'text'-Feld: {str(payload)[:200]}")
        return normalise(text)


# Eine ganze Datei dauert: gemessen 7 s (Voxtral) und 19 s (Whisper) für elf
# Minuten. Drei Stunden liegen damit bei einigen Minuten - mit Luft gerechnet.
_FILE_TIMEOUT = httpx.Timeout(900.0, connect=10.0)


async def _post_file(url: str, api_key: str | None, data: dict, filename: str,
                     payload: bytes, content_type: str) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=_FILE_TIMEOUT) as client:
        response = await client.post(url, headers=headers, data=data,
                                     files={"file": (filename, payload, content_type)})
    if response.status_code >= 400:
        raise TranscriptionError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()


def _segments(payload: dict) -> list[dict]:
    segmente = payload.get("segments")
    if not isinstance(segmente, list):
        text = normalise(str(payload.get("text") or ""))
        return [{"start": 0.0, "end": 0.0, "text": text}] if text else []
    ergebnis = []
    for s in segmente:
        if not str(s.get("text") or "").strip():
            continue
        segment = {"start": float(s.get("start") or 0), "end": float(s.get("end") or 0),
                   "text": normalise(str(s.get("text") or ""))}
        if s.get("speaker_id"):
            segment["speaker"] = str(s["speaker_id"])
        ergebnis.append(segment)
    return ergebnis


class WhisperCppTranscription(_HTTPTranscription):
    """whisper.cpp whisper-server (lokal, Metal)."""

    path = "/inference"
    send_model = False  # Das Modell steht beim Serverstart fest.


class OpenAICompatibleTranscription(_HTTPTranscription):
    """Beliebiger OpenAI-kompatibler Endpunkt."""

    async def transcribe_file(self, data: bytes, filename: str, content_type: str,
                              diarize: bool = False) -> list[dict]:
        if diarize:
            raise TranscriptionError("Dieser Dienst kann keine Sprecher unterscheiden.")
        felder = {**self.form_fields(), "response_format": "verbose_json",
                  "timestamp_granularities[]": "segment"}
        return _segments(await _post_file(self.base_url + self.path, self.api_key,
                                          felder, filename, data, content_type))


class MistralTranscription(_HTTPTranscription):
    """Mistral Voxtral als Datei-Dienst (Cloud).

    Mistrals Transkriptions-Endpunkt ist dem OpenAI-Pfad nachgebaut, aber nicht
    parameteridentisch: `response_format` und `prompt` nimmt er widerspruchslos
    entgegen und ignoriert sie. Der Vokabular-Hinweis heißt hier `context_bias`
    und wirkt. Laut Dokumentation ist er auf Englisch ausgelegt und für andere
    Sprachen experimentell; für Deutsch hat er in der Messung getan, was er
    soll.
    """

    def form_fields(self) -> dict[str, str | list[str]]:
        data: dict[str, str | list[str]] = {
            "model": self.model, "language": self.language,
        }
        begriffe = context_bias_items(self.vocabulary)
        if begriffe:
            data["context_bias"] = begriffe
        return data

    diarize = True

    async def transcribe_file(self, data: bytes, filename: str, content_type: str,
                              diarize: bool = False) -> list[dict]:
        # Zeitstempel und `language` vertragen sich - entgegen der Doku,
        # gemessen 2026-09-23. `diarize` verlangt Zeitstempel (sonst 422).
        felder = {**self.form_fields(), "timestamp_granularities": "segment"}
        if diarize:
            felder["diarize"] = "true"
        return _segments(await _post_file(self.base_url + self.path, self.api_key,
                                          felder, filename, data, content_type))


# Ein einzelnes input_audio.append darf höchstens 262144 Byte tragen
# (Angabe der API). Die Blöcke aus dem Browser sind viel kleiner, geteilt wird
# trotzdem - ein Rückstau darf nicht in einen Protokollfehler laufen.
_MAX_AUDIO_BYTES = 240_000
_RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0)


class MistralRealtimeTranscription(StreamingTranscriptionClient):
    """Voxtral Realtime (Mistral, Cloud) über WebSocket.

    Protokoll, gemessen gegen api.mistral.ai (Stand 2026-09):

      Server -> Client  session.created, session.updated,
                        transcription.text.delta {text}, transcription.done,
                        error {error: {message, code}}
      Client -> Server  session.update {session}, input_audio.append {audio},
                        input_audio.flush, input_audio.end

    Audio geht als base64-kodiertes rohes PCM, nicht als WAV.

    Zwei Eigenschaften des Modells, die den Aufbau bestimmen:

    * Es erfindet auf Stille nichts. Der Sprachanteil-Test, den das gestückelte
      Whisper braucht, entfällt hier.
    * Es kennt weder Sprachvorgabe noch Vokabular ("does not support context
      biasing", Fehlercode 3051) - als einziger der drei Dienste. Wer eine
      Begriffsliste braucht, nimmt Whisper oder Voxtral als Datei.
    """

    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 sample_rate: int = 16000, target_delay_ms: int = 500):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.sample_rate = sample_rate
        # Wie lange der Dienst sammeln darf, bevor er Text schickt. Kleiner
        # heißt früher sichtbar, aber weniger Kontext je Entscheidung.
        self.target_delay_ms = target_delay_ms

    @property
    def url(self) -> str:
        parsed = urlparse(self.base_url)
        schema = {"https": "wss", "http": "ws"}.get(parsed.scheme, parsed.scheme)
        return urlunparse(parsed._replace(
            scheme=schema,
            path=parsed.path.rstrip("/") + "/v1/audio/transcriptions/realtime",
            query=f"model={self.model}",
        ))

    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[str]:
        # Eine abgerissene Verbindung darf eine laufende Besprechung nicht
        # beenden. Neu verbinden und weitermachen; das Audio der Lücke ist
        # verloren, der Rest der Sitzung nicht.
        #
        # Die Aufnahme wird dafür genau einmal gelesen, von einer eigenen
        # Aufgabe, und in eine Warteschlange gelegt, aus der jede Verbindung
        # liest. Früher las die Sende-Aufgabe den Generator selbst; beim Abriss
        # wurde sie abgebrochen, und der Abbruch beendete den Generator gleich
        # mit. Die neue Verbindung fand dann eine erschöpfte Quelle vor,
        # schickte sofort "Ende" - und die Sitzung lief ohne Text weiter.
        # So bleibt auch das Audio aus der Wartezeit bis zur neuen Verbindung
        # erhalten; verloren ist höchstens der Block, der gerade unterwegs war.
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for chunk in audio:
                    queue.put_nowait(chunk)
            finally:
                queue.put_nowait(None)

        pumpe = asyncio.create_task(pump())
        state = {"exhausted": False}
        versuch = 0
        try:
            while True:
                try:
                    async for text in self._run_once(queue, state):
                        versuch = 0
                        yield text
                    return
                except TranscriptionError:
                    raise  # Fachlicher Fehler (falscher Schlüssel, falsches Modell)
                except Exception as exc:  # Verbindungsabbruch, DNS, Zeitüberschreitung
                    if state["exhausted"]:
                        return  # Audio war ohnehin zu Ende
                    if versuch >= len(_RECONNECT_DELAYS):
                        raise TranscriptionError(
                            f"Verbindung zu {self.model} wiederholt abgerissen: {exc}"
                        ) from exc
                    pause = _RECONNECT_DELAYS[versuch]
                    versuch += 1
                    logger.warning("Realtime-Verbindung abgerissen (%s), neuer Versuch in %.0f s",
                                   exc, pause)
                    await asyncio.sleep(pause)
        finally:
            pumpe.cancel()
            with suppress(asyncio.CancelledError):
                await pumpe

    async def _run_once(self, audio: asyncio.Queue,
                        state: dict) -> AsyncIterator[str]:
        from websockets.asyncio.client import connect  # nur für Strom-Dienste nötig

        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with connect(self.url, additional_headers=headers,
                           open_timeout=15, max_queue=1024) as ws:
            await self._handshake(ws)
            sender = asyncio.create_task(self._send(ws, audio, state))
            try:
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "transcription.text.delta":
                        yield event.get("text", "")
                    elif kind == "transcription.done":
                        return
                    elif kind == "error":
                        raise TranscriptionError(_error_message(event))
            finally:
                sender.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await sender

    async def _handshake(self, ws) -> None:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            event = json.loads(raw)
            if event.get("type") == "session.created":
                break
            if event.get("type") == "error":
                raise TranscriptionError(_error_message(event))
        await ws.send(json.dumps({"type": "session.update", "session": {
            "audio_format": {"encoding": "pcm_s16le", "sample_rate": self.sample_rate},
            "target_streaming_delay_ms": self.target_delay_ms,
        }}))

    async def _send(self, ws, audio: asyncio.Queue, state: dict) -> None:
        while (chunk := await audio.get()) is not None:
            for i in range(0, len(chunk), _MAX_AUDIO_BYTES):
                teil = chunk[i: i + _MAX_AUDIO_BYTES]
                await ws.send(json.dumps({
                    "type": "input_audio.append",
                    "audio": base64.b64encode(teil).decode("ascii"),
                }))
        state["exhausted"] = True
        await ws.send(json.dumps({"type": "input_audio.flush"}))
        await ws.send(json.dumps({"type": "input_audio.end"}))


def _error_message(event: dict) -> str:
    fehler = event.get("error")
    if isinstance(fehler, dict):
        nachricht = fehler.get("message")
        if isinstance(nachricht, str):
            code = fehler.get("code")
            return f"{nachricht} (Code {code})" if code else nachricht
    return f"Fehler vom Realtime-Dienst: {str(event)[:200]}"


_TYPES = {
    "whisper-cpp": WhisperCppTranscription,
    "openai-compatible": OpenAICompatibleTranscription,
    "mistral": MistralTranscription,
    "mistral-realtime": MistralRealtimeTranscription,
}


def create_transcription_client(
    type_: str, base_url: str, model: str, api_key: str | None = None,
    vocabulary: str = "", realtime_delay_ms: int = 500,
) -> TranscriptionClient | StreamingTranscriptionClient:
    try:
        cls = _TYPES[type_]
    except KeyError:
        raise ValueError(f"Unbekannter Transkriptionstyp: {type_}") from None
    if issubclass(cls, StreamingTranscriptionClient):
        # Ein Strom-Dienst kennt kein Vokabular-Feld, dafür eine Zielverzögerung.
        return cls(base_url=base_url, model=model, api_key=api_key,
                   target_delay_ms=realtime_delay_ms)
    return cls(base_url=base_url, model=model, api_key=api_key, vocabulary=vocabulary)


def create_from_engine(engine, vocabulary: str = ""):
    """Baut den Client aus einem Engine-Eintrag der Konfiguration."""
    return create_transcription_client(
        engine.type, engine.url, engine.model, engine.api_key, vocabulary,
        getattr(engine, "realtime_delay_ms", 500),
    )
