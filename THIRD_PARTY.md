# Fremdbestandteile

Live-Protokoll selbst steht unter der MIT-Lizenz (`LICENSE`). Diese Teile
stammen von anderen und stehen unter ihren eigenen Lizenzen:

| Bestandteil | Wo | Lizenz | Text |
|---|---|---|---|
| **lamejs** 1.2.1 ([github.com/zhuker/lamejs](https://github.com/zhuker/lamejs)), JavaScript-Fassung von **LAME** ([lame.sourceforge.net](https://lame.sourceforge.net)) | `static/lame.min.js`, Repository und Bündel | LGPL-3.0 | `licenses/LGPL-3.0.txt` (ergänzt `licenses/GPL-3.0.txt`), Hinweis der Autoren in `licenses/lamejs/LICENSE` |
| **whisper.cpp** (ggml-org) | `whisper-server` in den Desktop-Bündeln | MIT | `licenses/whisper.cpp/LICENSE` |
| **python-docx**, **lxml** und die übrigen Python-Abhängigkeiten | Bündel und Container | MIT, BSD u. a. | jeweils im mitgelieferten Paket (`*.dist-info`) |

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

## Whisper-Modell

Das Sprachmodell (`ggml-large-v3-turbo-q5_0.bin`) gehört nicht zu diesem
Repository und nicht zu den Bündeln; die Desktop-Fassung lädt es beim ersten
Start von Hugging Face. Es steht unter MIT.
