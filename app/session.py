"""Eine laufende Aufnahme-Sitzung.

Der Browser schickt rohes PCM (16 kHz mono, int16) über WebSocket. Von dort
gibt es zwei Wege, je nach gewähltem Dienst:

* **Gestückelt** (Whisper, Voxtral als Datei): ein Hintergrund-Worker schneidet
  an Sprechpausen Segmente und transkribiert sie einzeln.
* **Im Strom** (Voxtral Realtime): das Audio geht ungeschnitten in eine
  stehende Verbindung, der Text kommt wortweise zurück und wird hier zu
  Absätzen gebündelt.

In beiden Fällen stößt die Sitzung in konfigurierbaren Abständen die
Protokoll-Fortschreibung an.

Anders als bei einem Stapelbetrieb ist diese Sitzung ZUSTANDSBEHAFTET
und schreibt laufend auf Platte: eine einstündige Besprechung darf nicht an
einem Browser-Absturz verloren gehen.
"""

from __future__ import annotations

import asyncio
import re
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.audio import pcm_to_wav
from app.config import SAMPLE_RATE, Settings
from app.llm import AllEndpointsFailed, ChatClient
from app.output_types import OutputType
from app.protocol.strategies import Strategy, create_strategy
from app.segmenter import find_cut_point, speech_ratio
from app.transcription import (
    SentenceAssembler,
    StreamingTranscriptionClient,
    TranscriptionClient,
    TranscriptionError,
)

logger = logging.getLogger(__name__)


def wortmuster(wort: str) -> re.Pattern:
    """Das Wort als ganzes Wort - "Meier" trifft nicht "Meierhof"."""
    return re.compile(rf"(?<!\w){re.escape(wort)}(?!\w)")


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"

# Unter diesem Sprachanteil wird nicht transkribiert: Whisper erfindet auf
# Stille zuverlässig Text. Voxtral Realtime tut das nicht, dort entfällt der Test.
MIN_SPEECH_RATIO = 0.05

# Wie weit der Text eines Strom-Dienstes dem Audio hinterherhängt. Gemessen bei
# 500 ms Zielverzögerung: rund 1,3 s. Nur für den Zeitstempel im Transkript -
# ohne die Korrektur wäre jede Marke systematisch zu spät.
STREAM_LAG_SECONDS = 1.3


class LiveSession:
    def __init__(
        self,
        output_type: OutputType,
        strategy_name: str,
        transcription: TranscriptionClient,
        chat: ChatClient,
        settings: Settings,
        live_protocol: bool = True,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.output_type = output_type
        self.strategy_name = strategy_name
        self.strategy: Strategy = create_strategy(strategy_name, chat, output_type)
        self.transcription = transcription
        self.streaming = isinstance(transcription, StreamingTranscriptionClient)
        self.settings = settings
        # Aus heißt: kein einziger Protokolllauf während der Aufnahme und kein
        # Abschlusslauf. Das Transkript läuft trotzdem, und die Hinweise des
        # Nutzers werden gesammelt - sie wirken in der Zusammenfassung.
        self.live_protocol = live_protocol

        # Die gehostete Fassung schreibt nichts auf die Platte: fremde
        # Besprechungen sollen dort nicht liegen bleiben, auch nicht bis zum
        # nächsten Aufräumen. Preis ist, dass ein Neuladen des Tabs die Sitzung
        # endgültig verliert - beim eigenen Rechner sind es dagegen die eigenen
        # Dateien des Nutzers, dort wird weiter gespeichert.
        self.persistent = not settings.hosted
        self.dir = settings.sessions_dir / self.id if self.persistent else None
        self._audio_file = None
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._audio_file = (self.dir / "audio.raw").open("ab")

        self._buffer = bytearray()
        self._audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self.transcript: list[dict] = []
        # Was vor dem Termin über ihn bekannt ist; siehe set_context.
        self.context: dict[str, str] = {}
        self.running = False
        self.paused = False
        self.cycle = 0
        self.feedback: list[str] = []

        self._started_at: float | None = None
        self._audio_seconds = 0.0      # empfangen
        self._consumed_seconds = 0.0   # bereits zu Segmenten geschnitten
        self._chars_since_update = 0
        # Bis wohin das Transkript im Protokoll steckt. Ein Index statt einer
        # Zeichenzahl: Lückenvermerke und Trennzeichen zählen sonst nicht mit,
        # und der Anfang des neuen Texts fiel hinten herunter.
        self._processed_entries = 0
        self._last_update = time.monotonic()
        # Wann die Sitzung zuletzt benutzt wurde - danach richtet sich, wann
        # eine beendete Sitzung aus dem Speicher fliegt.
        self.last_activity = time.monotonic()
        self._stop_task: asyncio.Task | None = None
        self.failed: str | None = None
        self._worker_task: asyncio.Task | None = None
        self._protocol_task: asyncio.Task | None = None
        self._skip_live_pass = False
        self._live_run: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()
        # Wird nach dem Abschluss einmal gerufen, etwa fürs Verbrauchsprotokoll.
        self.on_finish = None
        # Korrekturen des Nutzers ("Meier" -> "Maier"): gelten für das bisherige
        # Transkript und für alles, was noch kommt.
        self.corrections: list[tuple[str, str]] = []
        self._write_meta()

    # -- Ereignisse ------------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def listeners(self) -> int:
        return len(self._subscribers)

    @property
    def audio_seconds(self) -> float:
        return self._audio_seconds

    @property
    def stopped(self) -> bool:
        return self._stop_task is not None and self._stop_task.done()

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def _emit(self, event: dict) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    # -- Aufnahme --------------------------------------------------------

    def start(self) -> None:
        # Eine beendete Sitzung läuft nicht wieder an - auch nicht, wenn sich
        # nach einem Abriss der Browser zurückmeldet, nachdem sie schon zu ist.
        if self.running or self._stop_task is not None:
            return
        self.running = True
        self._started_at = time.monotonic()
        self._last_update = time.monotonic()
        if self.streaming:
            # Zwei Aufgaben statt einer: der Protokolllauf dauert Sekunden, und
            # solange er läuft, darf niemand aufhören, die Verbindung zu lesen.
            self._worker_task = asyncio.create_task(self._stream_worker())
            if self.live_protocol:
                self._protocol_task = asyncio.create_task(self._protocol_worker())
        else:
            self._worker_task = asyncio.create_task(self._worker())
        self._worker_task.add_done_callback(self._worker_done)

    def _worker_done(self, task: asyncio.Task) -> None:
        """Endet der Worker, während die Aufnahme noch läuft, ist etwas
        schiefgegangen. Dann die Sitzung beenden, statt sie als laufend stehen
        zu lassen - sonst nimmt niemand mehr Audio ab, niemand prüft die
        Zeitgrenze, und der Platz des Benutzers bleibt belegt."""
        if task.cancelled() or not self.running:
            return
        exc = task.exception()
        self.failed = str(exc) if exc else "Die Transkription ist unerwartet beendet."
        logger.error("Sitzung %s: Worker beendet, Sitzung wird geschlossen: %s",
                     self.id, self.failed)
        self._emit({"type": "error", "stage": "transcription", "message": self.failed})
        self._end_soon()

    def _end_soon(self) -> None:
        """Die Sitzung von innen beenden. stop() wartet auf den Worker, also
        darf der Worker es nicht selbst abwarten - es läuft als eigene Aufgabe."""
        self.running = False
        asyncio.get_running_loop().create_task(self.stop())

    def pause(self) -> None:
        """Aufnahme anhalten. Der Puffer bleibt stehen, laufende Segmente
        werden noch fertig verarbeitet."""
        if self.paused:
            return
        self.paused = True
        self._emit({"type": "partial", "text": ""})
        self._emit({"type": "paused"})

    def resume(self) -> None:
        if not self.paused:
            return
        self.paused = False
        # Auslösefenster neu starten, sonst feuert die Pause den nächsten Lauf.
        self._last_update = time.monotonic()
        self._emit({"type": "resumed"})

    async def add_feedback(self, text: str) -> str:
        """Hinweis des Nutzers zur Protokollführung übernehmen.

        Geht bewusst NICHT ins Transkript: es ist eine Anweisung über das
        Protokoll, nicht Teil des aufgezeichneten Gesprächs.
        """
        text = text.strip()
        if not text:
            return self.strategy.protocol
        self.feedback.append(text)
        if not self.live_protocol:
            # Ohne Live-Protokoll gibt es nichts zum Einarbeiten - der Hinweis
            # geht in die Zusammenfassung nach dem Termin.
            self.strategy.feedback.append(text)
            return self.strategy.protocol
        self._emit({"type": "feedback_pending", "text": text})
        try:
            protocol = await self.strategy.apply_feedback(text)
        except AllEndpointsFailed as exc:
            self._emit({"type": "error", "stage": "llm", "message": str(exc)})
            return self.strategy.protocol
        self._emit({"type": "protocol", "cycle": self.cycle, "markdown": protocol,
                    "endpoint": "feedback", "prompt_tokens": 0,
                    "completion_tokens": 0, "ops": 0})
        self._persist()
        return protocol

    async def add_audio(self, pcm: bytes) -> None:
        self.last_activity = time.monotonic()
        if self.paused:
            return  # serverseitig verwerfen, unabhängig davon was der Browser sendet
        if self.streaming:
            self._audio_queue.put_nowait(pcm)
        else:
            async with self._lock:
                self._buffer.extend(pcm)
        if self._audio_file is not None:
            self._audio_file.write(pcm)
        self._audio_seconds += len(pcm) / 2 / SAMPLE_RATE

    async def stop(self, live_pass: bool = True) -> None:
        """Aufnahme beenden, Restpuffer verarbeiten, letzten Protokolllauf machen.

        `live_pass=False` lässt die letzten Läufe des Live-Modells aus und bricht
        einen laufenden ab: Wer gleich zusammenfassen lässt, wartet sonst auf ein
        Protokoll, das die Zusammenfassung ersetzt. Transkribiert wird trotzdem
        zu Ende.

        Darf beliebig oft gerufen werden, der Abschluss läuft genau einmal.
        Der Browser schickt den Stopp über den WebSocket und zusätzlich per
        POST, dazu kommen Zeitgrenze und Verbindungsabbruch. Ohne diese Sperre
        liefen zwei Abschlussläufe nebeneinander, und der zweite überschrieb
        den ersten. Die Aufgabe entsteht vor dem ersten await, damit ein
        zweiter Aufruf sie in jedem Fall schon vorfindet.
        """
        self.last_activity = time.monotonic()
        self.running = False  # sofort: ab hier nimmt die Sitzung nichts mehr an
        if not live_pass:
            self._skip_live_pass = True
            if self._live_run is not None:
                self._live_run.cancel()
        if self._stop_task is None:
            self._stop_task = asyncio.get_running_loop().create_task(self._finish())
        await asyncio.shield(self._stop_task)

    async def _finish(self) -> None:
        self.running = False
        for name in ("_worker_task", "_protocol_task"):
            task = getattr(self, name)
            if task is None:
                continue
            try:
                await task
            except Exception:  # noqa: BLE001 - gemeldet hat es _worker_done
                pass
            setattr(self, name, None)
        if self.live_protocol and not self._skip_live_pass:
            if self._chars_since_update > 0:
                await self._update_protocol()
            if self.settings.final_pass:
                await self._polish()
        if self._audio_file is not None:
            self._audio_file.close()
        self._persist()
        self.last_activity = time.monotonic()
        if self.on_finish is not None:
            try:
                self.on_finish(self)
            except Exception:  # noqa: BLE001 - der Abschluss selbst steht
                logger.exception("Nachlauf der Sitzung %s fehlgeschlagen", self.id)
        self._emit({"type": "stopped", "session": self.id})

    # -- Worker ----------------------------------------------------------

    def _over_time_limit(self) -> bool:
        """Im gehosteten Betrieb zahlt der Schlüssel des Betreibers für alle -
        ein vergessener offener Tab darf nicht unbegrenzt weiterlaufen.

        Gezählt wird das aufgenommene Audio, nicht die Wanduhr: Bezahlt wird
        je Minute Audio, und ein Ganztagsworkshop mit langen Pausen soll nicht
        an der Mittagspause scheitern."""
        limit = self.settings.max_session_minutes
        if not self.settings.hosted or limit <= 0 or self._started_at is None:
            return False
        return self._audio_seconds >= limit * 60

    def _hit_time_limit(self) -> None:
        """Zeitgrenze erreicht: geordnet beenden wie beim Stopp-Knopf, mit
        Restpuffer und Abschlusslauf."""
        logger.info("Sitzung %s hat die Zeitgrenze erreicht", self.id)
        self._emit({"type": "limit_reached",
                    "minutes": self.settings.max_session_minutes})
        self._end_soon()

    async def _worker(self) -> None:
        while True:
            if self.running and self._over_time_limit():
                # Nicht selbst stop() abwarten - das wartet auf genau diese
                # Schleife. _hit_time_limit startet es als eigene Aufgabe.
                self._hit_time_limit()

            async with self._lock:
                cut = find_cut_point(
                    bytes(self._buffer),
                    SAMPLE_RATE,
                    self.settings.segment_min_seconds,
                    self.settings.segment_max_seconds,
                )
                if cut is None and not self.running and self._buffer:
                    cut = len(self._buffer)  # Restpuffer am Ende leeren
                if cut is not None:
                    segment = bytes(self._buffer[:cut])
                    del self._buffer[:cut]
                else:
                    segment = b""
                segment_start = self._consumed_seconds
                self._consumed_seconds += len(segment) / 2 / SAMPLE_RATE

            if not segment:
                if not self.running:
                    return
                await asyncio.sleep(0.5)
                continue

            await self._handle_segment(segment, segment_start)

            if self._should_update():
                await self._update_protocol()

    async def _handle_segment(self, segment: bytes, start_seconds: float) -> None:
        if speech_ratio(segment, SAMPLE_RATE) < MIN_SPEECH_RATIO:
            logger.debug("Segment ohne Sprachanteil übersprungen")
            return
        self._emit({"type": "transcribing", "seconds": round(start_seconds, 1)})
        try:
            text = await self.transcription.transcribe(pcm_to_wav(segment, SAMPLE_RATE))
        except TranscriptionError as exc:
            logger.error("Transkription fehlgeschlagen: %s", exc)
            self._emit({"type": "error", "stage": "transcription", "message": str(exc)})
            return
        self._add_entry(text, start_seconds)

    def correct(self, alt: str, neu: str, vocabulary: str | None = None) -> int:
        """Eine Korrektur übernehmen: im bisherigen Transkript ersetzen und
        für jeden weiteren Absatz merken. Liefert die Zahl der Ersetzungen.

        Mit `vocabulary` bekommt auch die Erkennung die neue Begriffsliste -
        sofern der Dienst eine nimmt. Voxtral Realtime nimmt keine; dort ist
        das Ersetzen der einzige Weg."""
        if vocabulary is not None and not self.streaming \
                and hasattr(self.transcription, "vocabulary"):
            self.transcription.vocabulary = vocabulary
        alt, neu = alt.strip(), neu.strip()
        if not alt or alt == neu:
            return 0
        self.corrections = [(a, n) for a, n in self.corrections if a != alt] + [(alt, neu)]
        muster = wortmuster(alt)
        anzahl = 0
        for entry in self.transcript:
            neuer_text, n = muster.subn(neu, entry["text"])
            if n:
                entry["text"] = neuer_text
                anzahl += n
        self._persist_transcript()
        return anzahl

    def _add_entry(self, text: str, start_seconds: float) -> None:
        """Einen fertigen Transkript-Absatz übernehmen, egal woher er kommt."""
        for alt, neu in self.corrections:
            text = wortmuster(alt).sub(neu, text)
        if not text:
            return
        entry = {
            "text": text,
            "seconds": round(max(0.0, start_seconds), 1),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.transcript.append(entry)
        self._chars_since_update += len(text)
        self._emit({"type": "transcript", "entry": entry})
        self._persist_transcript()

    def set_context(self, context: dict) -> None:
        """Terminrahmen setzen oder nachreichen.

        Nachreichbar mit Absicht: Wer fünf Minuten zu spät in den Termin
        stolpert, darf kein Formular vorfinden - er startet die Aufnahme und
        trägt nach, sobald Luft ist. Der Rahmen gilt dann ab dem nächsten
        Protokolllauf.
        """
        erlaubt = ("topic", "agenda", "participants", "goal")
        self.context = {
            key: str(context.get(key) or "").strip()
            for key in erlaubt
            if str(context.get(key) or "").strip()
        }
        self.strategy.context = self.context
        self._persist()

    def note_gap(self, seconds: int, reason: str = "unterbrochen") -> None:
        """Eine Lücke im Mitschnitt vermerken.

        Der Browser hat die Tonaufnahme angehalten bekommen - diese Sekunden
        fehlen unwiederbringlich. Ohne den Vermerk stünde im Transkript ein
        nahtlos wirkender Text mit einem Loch, und das Protokollmodell
        formulierte darüber hinweg, ohne dass es jemand merkt.

        Kein _emit: Die Oberfläche zeigt die Lücke bereits an, sobald sie sie
        bemerkt. Und kein _chars_since_update - ein Loch ist kein Inhalt und
        soll keinen Protokolllauf auslösen.
        """
        if seconds <= 0:
            return
        text = (f"[Diktat - etwa {seconds} s der Aufnahme fehlen]" if reason == "diktat"
                else f"[Aufnahme unterbrochen - etwa {seconds} s fehlen]")
        self.transcript.append({
            "text": text,
            "seconds": round(self._audio_seconds, 1),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "gap": True,
        })
        self._persist_transcript()

    # -- Worker für Strom-Dienste ----------------------------------------

    async def _audio_source(self):
        """Die laufende Aufnahme als Strom von PCM-Blöcken.

        Endet, wenn die Aufnahme endet - daraufhin schließt der Dienst die
        Transkription ab. Hier wird auch die Zeitgrenze geprüft: diese Schleife
        läuft ohnehin ununterbrochen mit.
        """
        while True:
            if self.running and self._over_time_limit():
                self._hit_time_limit()
            try:
                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self.running:
                    return
                continue
            self._consumed_seconds += len(chunk) / 2 / SAMPLE_RATE
            yield chunk

    async def _stream_worker(self) -> None:
        """Wortweise Textstücke entgegennehmen und zu Absätzen bündeln."""
        assembler = SentenceAssembler()
        begonnen_bei: float | None = None
        try:
            async for delta in self.transcription.stream(self._audio_source()):
                if not delta:
                    continue
                if begonnen_bei is None:
                    begonnen_bei = self._consumed_seconds - STREAM_LAG_SECONDS
                for text in assembler.feed(delta):
                    self._add_entry(text, begonnen_bei)
                    begonnen_bei = None
                self._emit({"type": "partial", "text": assembler.partial})
        except TranscriptionError as exc:
            # Der Dienst hat aufgegeben (Schlüssel, Modell oder wiederholter
            # Abriss). Weiter läuft hier nichts, also die Sitzung beenden.
            logger.error("Transkription fehlgeschlagen: %s", exc)
            self._emit({"type": "error", "stage": "transcription", "message": str(exc)})
            if self.running:
                self.failed = str(exc)
                self._end_soon()
        rest = assembler.flush()
        if rest:
            self._add_entry(rest, begonnen_bei if begonnen_bei is not None else 0.0)
        self._emit({"type": "partial", "text": ""})

    async def _protocol_worker(self) -> None:
        """Fortschreibung getrennt vom Textempfang.

        Ein Protokolllauf dauert Sekunden; liefe er im selben Ablauf wie der
        Textempfang, stauten sich währenddessen die Wörter in der Verbindung.
        """
        while self.running:
            await asyncio.sleep(1.0)
            if self._should_update():
                await self._update_protocol()

    def _should_update(self) -> bool:
        if not self.live_protocol or self._chars_since_update == 0 or self._skip_live_pass:
            return False
        if self._chars_since_update >= self.settings.trigger_chars:
            return True
        return (time.monotonic() - self._last_update) >= self.settings.trigger_seconds

    async def _update_protocol(self) -> None:
        # Lückenvermerke gehen mit: Das Modell soll sehen, dass hier etwas
        # fehlt, statt über das Loch hinweg zu formulieren.
        neu = self.transcript[self._processed_entries:]
        self._processed_entries = len(self.transcript)
        new_text = " ".join(e["text"] for e in neu)
        self._chars_since_update = 0
        self._last_update = time.monotonic()
        self.cycle += 1

        self._emit({"type": "protocol_pending", "cycle": self.cycle})
        # Eigene Aufgabe, damit der Stopp nur den Lauf abbrechen kann: Bei den
        # gestückelten Diensten hängt daran auch die restliche Transkription.
        self._live_run = asyncio.ensure_future(self.strategy.update(new_text, self.cycle))
        try:
            result = await self._live_run
        except asyncio.CancelledError:
            if not self._live_run.cancelled() or not self._skip_live_pass:
                raise
            return
        except AllEndpointsFailed as exc:
            logger.error("Protokoll-Lauf fehlgeschlagen: %s", exc)
            self._emit({"type": "error", "stage": "llm", "message": str(exc)})
            return
        finally:
            self._live_run = None

        self._emit({
            "type": "protocol",
            "cycle": self.cycle,
            "markdown": result.protocol,
            "endpoint": result.endpoint,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "ops": result.ops,
        })
        self._persist()

    async def _polish(self) -> None:
        """Einmaliger Abschlusslauf (nur Strategien, die einen brauchen)."""
        before = self.strategy.protocol
        try:
            after = await self.strategy.polish()
        except AllEndpointsFailed as exc:
            logger.error("Abschlusslauf fehlgeschlagen: %s", exc)
            return
        if after != before:
            self._emit({"type": "protocol", "cycle": self.cycle, "markdown": after,
                        "endpoint": "polish", "prompt_tokens": 0,
                        "completion_tokens": 0, "ops": 0})

    # -- Export ----------------------------------------------------------

    def export_markdown(self) -> str:
        """Protokoll und Originaltranskript in einer Datei."""
        created = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
        parts = [
            f"# {self.output_type.name}",
            "",
            f"Aufgezeichnet am {created} · Sitzung `{self.id}` · "
            f"Strategie `{self.strategy_name}`",
            "",
            self.strategy.protocol or "_Kein Protokoll erzeugt._",
            "",
        ]
        if self.feedback:
            parts += ["## Hinweise zur Protokollführung", ""]
            parts += [f"- {f}" for f in self.feedback]
            parts.append("")
        parts += ["---", "", "## Originaltranskript", ""]
        parts += [f"**{_mmss(e['seconds'])}** {e['text']}" for e in self.transcript]
        return "\n".join(parts).strip() + "\n"

    # -- Persistenz ------------------------------------------------------

    def _write_meta(self) -> None:
        if self.dir is None:
            return
        (self.dir / "meta.json").write_text(
            json.dumps({
                "id": self.id,
                "output_type": self.output_type.id,
                "strategy": self.strategy_name,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "sample_rate": SAMPLE_RATE,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _persist_transcript(self) -> None:
        if self.dir is None:
            return
        lines = [f"[{_mmss(e['seconds'])}] {e['text']}" for e in self.transcript]
        (self.dir / "transcript.md").write_text("\n\n".join(lines), encoding="utf-8")

    def _persist(self) -> None:
        if self.dir is None:
            return
        self._persist_transcript()
        (self.dir / "protocol.md").write_text(self.strategy.protocol, encoding="utf-8")
        if self.strategy_name == "delta":
            (self.dir / "state.json").write_text(
                json.dumps(self.strategy.state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
