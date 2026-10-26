#!/usr/bin/env bash
# Baut whisper.cpp aus den Quellen und packt die Anwendung.
#
# Der Homebrew-whisper-server ist NICHT mitlieferbar: er linkt gegen
# /opt/homebrew/opt/ggml/lib/... und liefe auf keinem fremden Rechner.
set -euo pipefail
cd "$(dirname "$0")/.."

WHISPER_VERSION="${WHISPER_VERSION:-v1.9.3}"
BUILD_DIR="build/whisper.cpp"
mkdir -p build/bin

if [ ! -x "build/bin/whisper-server" ]; then
  echo "==> whisper.cpp $WHISPER_VERSION bauen"
  [ -d "$BUILD_DIR" ] || git clone --depth 1 --branch "$WHISPER_VERSION" \
      https://github.com/ggml-org/whisper.cpp "$BUILD_DIR"
  cmake -S "$BUILD_DIR" -B "$BUILD_DIR/out" \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=OFF \
      -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON
  cmake --build "$BUILD_DIR/out" --config Release --target whisper-server -j
  find "$BUILD_DIR/out" -name "whisper-server" -type f -perm -u+x \
      -exec cp {} build/bin/ \;
fi

echo "==> Verbleibende Bibliotheks-Abhaengigkeiten"
if command -v otool >/dev/null; then
  otool -L build/bin/whisper-server | grep -v "/usr/lib/" | sed -n '2,20p' || true
fi

echo "==> Anwendung packen"
uv run --with pyinstaller pyinstaller --noconfirm --clean \
    --distpath build/dist --workpath build/work \
    build/live-protocol.spec

echo
echo "Fertig: build/dist/"
ls -1 build/dist/
