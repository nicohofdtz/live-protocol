# Fremdbestandteile

Live-Protokoll selbst steht unter der MIT-Lizenz (`LICENSE`). Diese Teile
stammen von anderen und stehen unter ihren eigenen Lizenzen:

| Bestandteil | Wo | Lizenz | Text |
|---|---|---|---|
| **lamejs** 1.2.1 ([github.com/zhuker/lamejs](https://github.com/zhuker/lamejs)), JavaScript-Fassung von **LAME** ([lame.sourceforge.net](https://lame.sourceforge.net)) | `static/lame.min.js`, Repository und Apps | LGPL-3.0 | `licenses/LGPL-3.0.txt` (ergänzt `licenses/GPL-3.0.txt`), Hinweis der Autoren in `licenses/lamejs/LICENSE` |
| **whisper.cpp** (ggml-org) | `whisper-server` in den Desktop-Apps | MIT | `licenses/whisper.cpp/LICENSE` |
| **Python-Abhängigkeiten** (FastAPI, Uvicorn, httpx, Pydantic, python-docx, lxml u. a.) | Apps und Container | MIT, BSD, Apache-2.0, MPL-2.0 (certifi) u. a. | Apps: `licenses/python-pakete.txt`, beim Bauen aus den Paketen erzeugt. Container: im jeweiligen Paket (`*.dist-info`) |
| **Python 3.14** (CPython, python-build-standalone) mit u. a. OpenSSL, libffi, zlib, expat, libmpdec, bzip2, xz, Zstandard | Apps | PSF-2.0, Apache-2.0 (OpenSSL), MIT, BSD, zlib, 0BSD | `licenses/python/` |
| **PyInstaller**-Bootloader | Apps | GPL-2.0 mit Ausnahme für gepackte Programme | `licenses/python-pakete.txt` |

## lamejs (LGPL-3.0)

`static/lame.min.js` ist unverändert aus dem npm-Paket `lamejs@1.2.1`
übernommen. Es kodiert den Mitschnitt im Browser zu MP3 und liegt als eigene
Datei vor, die der Browser getrennt lädt (`static/mp3-worker.js` bindet sie
per `importScripts` ein). Damit lässt sie sich jederzeit gegen eine andere
oder geänderte Fassung austauschen, wie es die LGPL verlangt: Datei
ersetzen, neu laden. Fehlt sie oder schlägt sie fehl, fällt die Anwendung auf
einen WAV-Mitschnitt zurück.

Der Quelltext von lamejs liegt unter
[github.com/zhuker/lamejs](https://github.com/zhuker/lamejs) und im npm-Paket.

## Python-Laufzeit

Die Apps bringen ihre eigene Python-Laufzeit mit. `licenses/python/cpython-license.rst`
ist die Lizenzseite der Python-Dokumentation (Python 3.14.3). Sie enthält die
PSF-Lizenz und die Texte der eingebundenen Bibliotheken, darunter OpenSSL,
expat, libffi, zlib und libmpdec. Für bzip2, xz und Zstandard liegen die Texte
daneben. SQLite ist gemeinfrei.

## Whisper-Modell

Das Sprachmodell (`ggml-large-v3-turbo-q5_0.bin`) gehört nicht zu diesem
Repository und nicht zu den Apps; die Desktop-Fassung lädt es beim ersten
Start von Hugging Face. Es steht unter MIT.
