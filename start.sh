#!/usr/bin/env bash
# Startet Whisper-Server und App zusammen; beendet beides gemeinsam.
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${WHISPER_MODEL:-$HOME/.local/share/whisper-models/ggml-large-v3-turbo-q5_0.bin}"
if [ ! -f "$MODEL" ]; then
  echo "Whisper-Modell fehlt: $MODEL" >&2
  echo "Holen mit: curl -L -o \"$MODEL\" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin" >&2
  exit 1
fi

# -sow: an Wortgrenzen umbrechen statt an Tokengrenzen (sonst "Whis per").
whisper-server -m "$MODEL" -l de --host 127.0.0.1 --port 8081 -t 4 -sow >/tmp/whisper-server.log 2>&1 &
WHISPER_PID=$!
trap 'kill $WHISPER_PID 2>/dev/null || true' EXIT

until curl -sf http://127.0.0.1:8081/ >/dev/null 2>&1 || ! kill -0 $WHISPER_PID 2>/dev/null; do
  sleep 0.5
done

echo "Whisper läuft (PID $WHISPER_PID, Log /tmp/whisper-server.log)"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
