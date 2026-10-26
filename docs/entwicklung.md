# Entwicklung, Versionen, Bündel

Ergänzung zur README für alle, die am Code arbeiten oder eigene Bündel bauen.

## Ohne Browser testen

```bash
ffmpeg -i aufnahme.m4a -ac 1 -ar 16000 -c:a pcm_s16le aufnahme.wav
uv run python tools/replay.py aufnahme.wav --strategy delta
```

Spielt die Datei durch dieselbe Pipeline, die auch der Browser benutzt.
Ergebnisse landen unter `~/.local/share/live-protocol/sessions/<id>/`.

## Versionen und Tags

- **Mittlere Stelle** (0.6.0) für neue Funktionen, **letzte Stelle** (0.6.1)
  für reine Fehlerbehebungen.
- **Ausgeliefert wird nur eine getaggte Version.** Fußzeile, „Neuigkeiten“
  und Downloads passen dann immer zusammen. Auch ein kleiner Fix bekommt
  dafür seine eigene Nummer.
- **Zu jeder Version gehören:** `version` in `pyproject.toml` und
  `__version__` in `app/version.py` (ein Test hält beide zusammen) und ein
  Abschnitt in `static/neuigkeiten.json` (ein Test verlangt ihn).

Ablauf: Nummer hochsetzen, Neuigkeiten schreiben, committen, `git tag -a
vX.Y.Z`, mit Tags pushen. Der Tag startet den Build der Mac- und
Windows-Fassung (`.github/workflows/build.yml`). Die CI legt dafür einen
**Release-Entwurf** an und hängt beide Bündel daran. Veröffentlicht wird der
Entwurf von Hand, nachdem die Bündel ausprobiert sind — erst dann meldet die
Update-Prüfung der Desktop-Fassungen die neue Nummer.

## Auslieferbare Fassung bauen

```bash
./build/build.sh          # baut whisper.cpp aus den Quellen, dann PyInstaller
open build/dist/          # Ergebnis: Live-Protokoll.app
```

**Der Homebrew-`whisper-server` ist nicht mitlieferbar** — er linkt gegen
`/opt/homebrew/opt/ggml/lib/…` und liefe auf keinem fremden Rechner. Das
Build-Skript baut whisper.cpp deshalb statisch aus den Quellen; die
mitgelieferte Binärdatei hängt danach nur noch an System-Frameworks
(Metal inklusive).

**Mindestens whisper.cpp v1.9.3.** Die eingebetteten Metal-Shader von v1.9.2
werden vom Metal-Compiler ab macOS 26 nicht mehr übersetzt (ein
Flash-Attention-Kernel), das Programm stürzt beim Laden des Modells ab.

Das Modell steckt **nicht** im Build: 574 MB plattformunabhängige Daten in
jedem Installer wären reine Bandbreitenverschwendung. Beim ersten Start lädt
die Anwendung sie von Hugging Face nach — mit Prüfsumme, fortsetzbar, und mit
Fortschrittsanzeige im Fenster, weil ein gebündeltes Programm keine sichtbare
Konsole hat.

### Plattformen

Ein plattformübergreifender Universal-Build ist nicht möglich: whisper.cpp und
die Python-Bündelung sind beide nativ. Auch ein macOS Universal Binary
scheidet praktisch aus, weil es ein universal2-Python samt universal2-
Abhängigkeiten voraussetzt. Es bleiben **zwei Fassungen**: macOS/Apple Silicon
und Windows x64. Vom Mac aus lässt sich die Windows-Fassung nicht erzeugen —
dafür ist `.github/workflows/build.yml` da.

**Intel-Macs sind bewusst nicht dabei.** Der Build für Intel-Macs ist in der
CI fehlgeschlagen und wird nicht gepflegt: Die lokale Fassung lebt davon, dass
large-v3-turbo schneller läuft als in Echtzeit — auf dem M1 sind es 6-7×,
Metal-beschleunigt. Ein Intel-Mac ohne diese Beschleunigung wäre genau da am
Anschlag, wo es darauf ankommt. Für solche Geräte ist die Server-Fassung im
Browser der richtige Weg; dort rechnet der Dienst.

### Fenster ohne Rahmen

Der Launcher sucht einen Chromium-Browser und öffnet ihn mit `--app=`, was ein
Fenster ohne Adressleiste und Tabs ergibt. Unter Windows greift das immer
(Edge ist vorinstalliert), unter macOS nur mit Chrome, Edge oder Brave —
**Safari kann es nicht**, dort wird es ein normaler Tab. Das Fenster läuft mit
eigenem Profil, deshalb beendet Schließen auch das Programm.

## Update-Prüfung

Die Desktop-Fassung fragt beim Start die GitHub-API nach dem neuesten
veröffentlichten Release dieses Repositorys
(`https://api.github.com/repos/nicohofdtz/live-protocol/releases/latest`).
Dort liegen auch die Bündel; eine eigene Gegenstelle braucht es nicht.
Entwürfe sieht die API nicht, eine Fassung gilt also erst als verfügbar, wenn
ihr Release veröffentlicht ist.

Die Abfrage läuft im Backend, nicht im Browser (das erspart CORS), und ist
folgenlos, wenn nichts erreichbar ist: offline gibt es schlicht keinen
Hinweis. Der Hinweis lässt sich wegklicken und kommt für dieselbe Version
nicht wieder. Abschaltbar mit `UPDATE_CHECK=false`. Wer eigene Bündel
verteilt, lässt `UPDATE_URL` auf eine andere Quelle zeigen: entweder dieselbe
Form wie die GitHub-API (`tag_name`, `html_url`) oder schlicht
`{"version": "0.7.0", "download_url": "https://…"}`.

