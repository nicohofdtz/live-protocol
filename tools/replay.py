"""Spielt eine WAV-Datei durch die echte Pipeline - ohne Browser.

Damit lassen sich Segmentierung, Transkription und Protokollstrategie an
aufgezeichnetem Material testen und vergleichen.

    uv run python tools/replay.py audio.wav --strategy delta
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import SAMPLE_RATE, settings  # noqa: E402
from app.llm import ChatClient  # noqa: E402
from app.output_types import load_output_types  # noqa: E402
from app.session import LiveSession  # noqa: E402
from app.transcription import create_from_engine  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    with wave.open(args.wav, "rb") as handle:
        if handle.getframerate() != SAMPLE_RATE or handle.getnchannels() != 1:
            print(f"WAV muss {SAMPLE_RATE} Hz mono sein "
                  f"(ist {handle.getframerate()} Hz, {handle.getnchannels()} Kanal/Kanäle).\n"
                  f"  ffmpeg -i EINGABE -ac 1 -ar {SAMPLE_RATE} -c:a pcm_s16le AUSGABE.wav")
            return 2
        pcm = handle.readframes(handle.getnframes())

    output_types = load_output_types()
    output_type = output_types[args.output_type]

    engines = {e.id: e for e in settings.transcription_engines}
    engine = engines.get(args.engine)
    if engine is None:
        print(f"Unbekannte Engine: {args.engine}. Verfügbar: {', '.join(engines)}")
        return 2
    print(f"Transkription über {engine.name}")
    chat = ChatClient(settings.llm_endpoints)

    for entry in await chat.health():
        state = "erreichbar" if entry["ok"] else f"NICHT erreichbar - {entry['error']}"
        print(f"LLM {entry['name']} ({entry['model']}): {state}")

    session = LiveSession(
        output_type=output_type,
        strategy_name=args.strategy or output_type.default_strategy,
        transcription=create_from_engine(engine, settings.vocabulary),
        chat=chat,
        settings=settings,
    )
    print(f"Sitzung {session.id} ({output_type.id} / {session.strategy_name}) -> {session.dir}\n")

    queue = session.subscribe()

    async def show() -> None:
        while True:
            event = await queue.get()
            if event["type"] == "transcript":
                print(f"[{event['entry']['seconds']:6.1f}s] {event['entry']['text'][:110]}")
            elif event["type"] == "protocol":
                print(f"\n--- Lauf {event['cycle']} über {event['endpoint']} "
                      f"({event['prompt_tokens']} Prompt-Tokens"
                      f"{', ' + str(event['ops']) + ' ops' if event['ops'] else ''}) ---")
                print(event["markdown"][:600])
                print("---\n")
            elif event["type"] == "error":
                print(f"FEHLER ({event['stage']}): {event['message'][:300]}")

    printer = asyncio.create_task(show())
    session.start()

    started = time.monotonic()
    frame = int(SAMPLE_RATE * 0.25) * 2  # 250-ms-Häppchen wie der Browser
    for offset in range(0, len(pcm), frame):
        await session.add_audio(pcm[offset : offset + frame])
        if args.realtime:
            await asyncio.sleep(0.25)
        else:
            await asyncio.sleep(0)

    await session.stop()
    printer.cancel()

    audio_seconds = len(pcm) / 2 / SAMPLE_RATE
    elapsed = time.monotonic() - started
    print(f"\n{audio_seconds:.0f}s Audio in {elapsed:.0f}s verarbeitet "
          f"({audio_seconds / elapsed:.1f}x Echtzeit), {session.cycle} Protokollläufe")
    print(f"Ergebnisse: {session.dir}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("wav")
    p.add_argument("--output-type", default="meeting")
    p.add_argument("--engine", default="whisper-local")
    p.add_argument("--strategy", default=None, choices=["rewrite", "delta"])
    p.add_argument("--realtime", action="store_true",
                   help="in Echtzeit einspielen statt so schnell wie möglich")
    raise SystemExit(asyncio.run(main(p.parse_args())))
