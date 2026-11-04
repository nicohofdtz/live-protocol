#!/usr/bin/env bash
# Spricht die Demo-Texte mit den macOS-Stimmen ein (say) und setzt sie mit
# ffmpeg zu je einer WAV-Datei zusammen: out/besprechung.wav, out/brainstorming.wav.
# Braucht die deutschen Premium-Stimmen (Systemeinstellungen > Bedienungshilfen >
# Gesprochene Inhalte); mit den Standardstimmen ist die Erkennung unbrauchbar.
set -euo pipefail
cd "$(dirname "$0")"

# macOS liefert bash 3.2 ohne assoziative Arrays aus.
stimme() {
  case "$1" in
    JULIA) echo "Anna (Premium)" ;; TOBIAS) echo "Markus (Enhanced)" ;;
    JONAS) echo "Yannick (Enhanced)" ;; LUKAS) echo "Petra (Premium)" ;;
    *) echo "Unbekannter Sprecher: $1" >&2; exit 1 ;;
  esac
}
# Die Sprechererkennung trennt nach Stimmlage: Markus und Yannick allein fallen
# zusammen, deshalb liegt Jonas tiefer.
filter_fuer() {
  case "$1" in
    JONAS) echo "asetrate=48000*0.82,aresample=48000,atempo=1.2195" ;;
    *) echo anull ;;
  esac
}
RATE=165        # Wörter pro Minute
PAUSE=0.7       # Sekunden Stille zwischen zwei Beiträgen

mkdir -p out
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

ffmpeg -loglevel error -f lavfi -i "anullsrc=r=48000:cl=mono" -t "$PAUSE" "$tmp/pause.wav"
ffmpeg -loglevel error -f lavfi -i "anullsrc=r=48000:cl=mono" -t 2 "$tmp/rand.wav"

for name in besprechung brainstorming; do
  liste="$tmp/$name.txt"
  echo "file '$tmp/rand.wav'" >"$liste"
  n=0
  while IFS= read -r zeile; do
    [[ -z "$zeile" || "$zeile" == \#* ]] && continue
    sprecher=${zeile%%:*}
    text=${zeile#*: }
    n=$((n + 1))
    say -v "$(stimme "$sprecher")" -r "$RATE" -o "$tmp/$name-$n.aiff" -- "$text"
    ffmpeg -loglevel error -i "$tmp/$name-$n.aiff" -ar 48000 -ac 1 \
      -af "$(filter_fuer "$sprecher")" "$tmp/$name-$n.wav"
    echo "file '$tmp/$name-$n.wav'" >>"$liste"
    echo "file '$tmp/pause.wav'" >>"$liste"
  done <"$name.txt"
  echo "file '$tmp/rand.wav'" >>"$liste"
  ffmpeg -loglevel error -y -f concat -safe 0 -i "$liste" -c:a pcm_s16le "out/$name.wav"
  printf '%s: %s Beiträge, %.0f s\n' "$name" "$n" \
    "$(ffprobe -v error -show_entries format=duration -of csv=p=0 "out/$name.wav")"
done
