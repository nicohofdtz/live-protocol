# Technik und Bedienung im Detail

Ergänzung zur README: wie die Anwendung aufgebaut ist, warum, und was die einzelnen Funktionen tun.

## Architektur

```
Browser                              Backend                    Dienste
───────                              ───────                    ───────
Mikrofon + Systemton (optional)
  └ getUserMedia, getDisplayMedia
      └ AudioWorklet (16 kHz PCM, Mono-Summe)
          └ WebSocket ──────────────→ (a) Strom ────────────→ Voxtral Realtime
                                        │  Wörter zurück      (stehende
                                        │  → Absätze           WebSocket-
                                        │                      Verbindung)
                                        └ (b) Puffer
                                            └ Segmentierung (Schnitt an Pausen)
                                                └ Transkription → whisper.cpp (lokal)
                                                │                 oder Cloud-Dienst
   linkes Fenster ←── Transkript ───────────────┤                 (Voxtral, OpenAI-
                                                │                  kompatibel)
                                                └ Trigger (Zeit ODER Zeichen)
                                                    └ Strategie ──→ Endpunktliste,
   rechtes Fenster ←── Protokoll ────────────────────┘             erster erreichbarer
                                                                   (OpenAI-kompatibel,
Nach dem Stopp: ganzes Transkript ──→ Zusammenfassung ──→           Mistral)
                                                       gewähltes Modell
```

Das Live-Protokoll während der Aufnahme ist eine Zugabe und standardmäßig
aus; maßgeblich ist die Zusammenfassung nach dem Stopp, die das
ganze Transkript auf einmal an das gewählte Modell gibt.

Der obere Weg (a) und der untere (b) schließen sich aus - welcher läuft,
entscheidet die gewählte Engine. Ab dem Transkript ist alles wieder gemeinsam.

Der Browser schickt **rohes PCM**, nicht MediaRecorder-Fragmente: webm/mp4-
Häppchen sind einzeln nicht dekodierbar, der Server müsste die wachsende
Datei sonst in jedem Zyklus komplett neu dekodieren. Auf localhost ist die
Bandbreite (~115 MB/h) belanglos, und serverseitig ist PCM beliebig schneidbar.

### Zwei Fortschreibungsstrategien

| Strategie | Vorgehen | gemessen (11 Zyklen, 16 Min.) |
|---|---|---|
| `rewrite` | Modell bekommt Protokoll + neues Segment und schreibt alles neu. Ein Hinweis im Systemprompt erklärt das bisherige Protokoll ausdrücklich zum veralteten Stand, der umgestellt und gekürzt werden darf. | bestes Protokoll; Latenz wächst 13 s -> 44 s |
| `delta` | Modell bekommt nur eine Themenübersicht + neues Segment und liefert Änderungsoperationen als JSON; gerendert wird deterministisch in Python. | konstant 3-6 s, ein Neuntel der Ausgabe-Tokens; etwas sachlicher |

Nach dem Stoppen läuft optional ein **Abschlusslauf** (`FINAL_PASS=true`), der
die gesammelte Struktur einmal glättet - so bekommt `delta` die konstante
Latenz während der Sitzung und trotzdem eine ausformulierte Endfassung.

Ist ein Modell für die Zusammenfassung eingerichtet, lässt der Stopp in der
Oberfläche den letzten Protokolllauf und den Abschlusslauf aus und bricht einen
laufenden ab (`live_pass=false`). Die Zusammenfassung entsteht aus dem Transkript
und ersetzt das Protokoll ohnehin. Wer stattdessen „Später“ wählt, dem holt
`/api/sessions/{id}/live-pass` die ausgelassenen Läufe im Hintergrund nach.

Empfehlung: `rewrite` für Besprechungen bis etwa 30 Minuten, `delta` +
Abschlusslauf für längere. Vollständige Messwerte und Begründung:
**`experiments/BEFUNDE.md`**.

## Bedienung

- **Pegelanzeige** in der Kopfleiste zeigt, dass das Mikrofon wirklich ankommt.
- **Pause** hält die Aufnahme an; pausiertes Audio wird serverseitig verworfen,
  unabhängig davon, was der Browser noch sendet.
- **Kopieren** je Fenster (Transkript bzw. Protokoll als Markdown).
- **Export** legt Protokoll, Hinweise und Originaltranskript in einer
  Markdown-Datei ab.
- **Einstellungen** (Zahnrad): Transkriptions-Engine, Zusammenfassungs-Modell,
  Eigennamen-Vokabular, Segmentlänge und Auslöseschwellen. Wird im Browser
  gemerkt und gilt ab der nächsten Aufzeichnung.

### Gestückelt oder im Strom

Die Transkriptions-Dienste sind nicht nur verschiedene Adressen, sie arbeiten
grundlegend verschieden:

| | gestückelt (Whisper, Voxtral als Datei) | im Strom (Voxtral Realtime) |
|---|---|---|
| Verbindung | eine Anfrage je Segment | eine stehende WebSocket-Verbindung |
| Schnitt | wir, an Sprechpausen | der Dienst selbst |
| erster Text | nach der Mindest-Segmentlänge (10 s) | nach gut einer Sekunde, wortweise |
| Stille | muss gefiltert werden, sonst erfindet Whisper Text | erzeugt nichts |
| Vokabular | ja, als Prompt | ja bei Voxtral als Datei (`context_bias`), **nein** bei Realtime |

Der Strom-Dienst liefert einzelne Wörter. Ein Absatz je Wort wäre unlesbar und
würde die Protokollführung mit Kleinstkram füttern, deshalb wird bis zum
Satzende gebündelt (mindestens 120, höchstens 600 Zeichen). Der noch offene
Satz steht währenddessen kursiv unter dem Transkript.

Reißt die Verbindung, wird bis zu viermal mit wachsendem Abstand neu verbunden.
Das Audio der Lücke ist verloren, die Sitzung nicht.

### Lokal oder Cloud

Beide Stufen sind einzeln umschaltbar:

| Stufe | lokal | Cloud |
|---|---|---|
| Transkription | whisper.cpp large-v3-turbo (Vorauswahl am eigenen Rechner) | Voxtral Realtime (`voxtral-mini-transcribe-realtime-2602`), Voxtral als Datei (`voxtral-mini-latest`), das Transkriptionsmodell des OpenAI-kompatiblen Anbieters (z. B. Whisper turbo über OpenRouter) |
| Live-Protokoll | OpenAI-kompatibler Anbieter im eigenen Netz, z. B. Ollama (`qwen3.5:9b`) | OpenAI-kompatibler Anbieter (z. B. Gemma 4 über OpenRouter), Mistral Nemo |
| Zusammenfassung | wie Live-Protokoll | GLM 5.3 bei Mistral in der EU mit zwei Denkstufen, das Modell des OpenAI-kompatiblen Anbieters |

Die Einträge entstehen aus zwei Anbietern: **Mistral** (ein Schlüssel, alles
inklusive der Mistral-eigenen Funktionen) und einem **OpenAI-kompatiblen**
Dienst (Adresse, Schlüssel, Modelle; Vorlagen für Mistral, OpenRouter,
OpenAI, Ollama und eigene Dienste). Mistral-eigen sind die Transkription in
Echtzeit, das Unterscheiden von Sprechern und `context_bias`; das Chat-Modell
spricht auch bei Mistral den OpenAI-Pfad `/v1/chat/completions`.

Beim Modell fürs Live-Protokoll gibt es zusätzlich **Automatisch**: dann wird
die Endpunktliste der Reihe nach durchprobiert und der erste erreichbare
genommen. Sinnvoll, weil ein Rechner im eigenen Netz nicht immer läuft. Wer
einen festen Endpunkt wählt, bekommt bewusst **kein** Ausweichen - eine
Sitzung soll nicht stillschweigend in die Cloud rutschen.

**Welche Mistral-Modelle antworten, hängt am Tarif des Kontos.** Ein Modell,
das der Tarif nicht abdeckt, antwortet mit HTTP 429 oder 403. Kontingente
gelten laut Dokumentation je Workspace für alle seine Schlüssel gemeinsam.

Mistral-Einträge brauchen einen Mistral-Schlüssel (in der `.env` oder in den
Einstellungen der Desktop-Fassung), Cloud-Einträge des OpenAI-kompatiblen
Anbieters dessen Schlüssel; ohne ihn sind sie im Menü gesperrt. Dienste im
eigenen Netz brauchen keinen. Die Oberfläche weist bei jedem Cloud-Eintrag darauf hin, was
den Rechner verlässt: bei der Transkription das Audio, bei der Zusammenfassung
Transkriptausschnitte.

### Feedback zum Protokoll

Sobald das erste Protokoll steht, erscheint unter dem rechten Fenster ein
Feedback-Feld: tippen oder diktieren, etwa „Punkt X kommt zu kurz, bitte
ausbauen" oder „das wurde falsch verstanden".

Ein Hinweis ist **Metainformation über die Protokollführung**, kein
Gesprächsinhalt - er landet deshalb nie im Transkript. Er wirkt sofort und
gilt danach dauerhaft für jeden weiteren Lauf.

Die `delta`-Strategie braucht dafür eine eigene Operation (`revise_topic`) und
sieht bei einem Hinweis einmalig das ganze Protokoll statt nur der
Themenübersicht - sonst könnte sie Bestehendes nur ergänzen, nicht überarbeiten.
Danach geht es mit der billigen Übersicht weiter.

### Eigennamen

Spracherkennung verfremdet gern seltene Eigennamen, Fachbegriffe und
Abkürzungen. Im Einstellungsmenü lässt sich deshalb eine Begriffsliste
hinterlegen, die der Erkennung als Hinweis mitgegeben wird und die
Schreibweise dieser Begriffe beeinflusst.

Wie die Liste beim Dienst ankommt, ist verschieden:

| Dienst | Feld | Form |
|---|---|---|
| Whisper (lokal) | `prompt` | freier Text |
| Voxtral als Datei | `context_bias` | **je Eintrag genau ein Wort** |
| Voxtral Realtime | — | wird zurückgewiesen |

Mistrals Datei-Endpunkt nimmt `prompt` und `response_format` zwar
widerspruchslos an, ignoriert sie aber; wirksam ist dort allein
`context_bias`. Kommas, Leerzeichen und leere Einträge quittiert er mit
HTTP 400 (Code 3051), deshalb wird die Liste hier in Einzelwörter zerlegt -
aus „Freiwillige Feuerwehr" werden zwei Begriffe, die einzeln weiterwirken.
Laut Dokumentation ist das Verfahren auf Englisch ausgelegt und für andere
Sprachen experimentell; für Deutsch hat es in der Messung getan, was es soll.

Voxtral Realtime weist sowohl `context_bias` als auch eine feste Sprachvorgabe
zurück („does not support context biasing", Code 3051). Dort ist das Feld
gesperrt statt wirkungslos.

## Mitschnitt und vorhandenes Audio

Während einer Aufnahme läuft ein **Mitschnitt** mit, den es nach dem Beenden
zum Herunterladen gibt — als Rückfallebene, wenn die Erkennung enttäuscht. Er
wird schon während der Aufnahme in einem Web Worker zu **MP3** kodiert (mono,
48 kbit/s, rund 22 MB je Stunde statt 115 MB als WAV). Bei 48 kbit/s erkennt
Voxtral 99,6 % derselben Wörter wie am WAV, bei 32 kbit/s 98,6 %. Pause und
Diktat fehlen im Mitschnitt genauso wie im Transkript, beide zählen dieselben
Sekunden. Lässt sich der Kodierer nicht laden, wird es wie früher ein WAV. Der
Mitschnitt liegt ausschließlich im Arbeitsspeicher des Browsers: Neuladen
oder Schließen wirft ihn weg, und das ist Absicht.

**Vorhandenes Audio** transkribiert der Knopf mit dem Pfeil. Bei einem
Cloud-Dienst geht die Datei **unverändert und am Stück** an den Server und
von dort an den Dienst, der Segmente mit Zeitstempeln zurückgibt. Gemessen
an elf Minuten: 7 s mit Voxtral, 19 s mit Whisper über OpenRouter. Der Server
liest die Datei als rohen Body in den Speicher, nicht als Formular, weil
Formular-Uploads ab 1 MB auf der Platte landen. Obergrenze `MAX_UPLOAD_MB`,
Vorgabe 150.

**Sprecher unterscheiden:** Nach dem Beenden, oder vorab in den Einstellungen
eingeschaltet, geht die Aufnahme mit `diarize` an Voxtral. Zurück kommt ein
Transkript mit Sprecherkennungen, das das bisherige ersetzt; Lückenvermerke
werden nach ihrer Zeit wieder eingefügt. Im Dialog bekommt jeder Sprecher
anhand seiner längsten Ausschnitte, die sich anhören lassen, einen Namen, oder
den Vermerk „mehrere Personen“. Im Transkript lässt sich danach nach Sprecher
filtern und jede Zeile per Klick auf den Namen umhängen. In der Zusammenfassung
ordnet „Aussagen Personen zuordnen“ Positionen und Aufgaben den Namen zu; ohne
die Option gehen keine Namen an das Modell.

**Protokoll nachbessern:** Das Stift-Symbol öffnet das fertige Protokoll als
Markdown zum Bearbeiten von Hand. Wer lieber sagt, was falsch ist, markiert die
Stelle und hinterlässt eine **Anmerkung** („gemeint ist Frau Maier“); mehrere
Anmerkungen gehen gesammelt in einem kleinen Lauf an das Modell. Es schreibt
das Protokoll nicht neu, sondern liefert nur Ersetzungen, die die Anwendung
selbst einsetzt – der Rest bleibt Zeichen für Zeichen, wie er war, und jede
Änderung lässt sich einzeln zurücknehmen.

**Export:** Protokoll oder Transkript als Markdown oder Word. Die Word-Datei
baut der Server mit python-docx, damit sie auch in der Desktop-Fassung ohne
Netz entsteht; gespeichert wird dabei nichts.

**Projekt speichern und öffnen:** Das Export-Menü speichert das Projekt als
ZIP — Audio plus `projekt.json` mit Transkript (samt Lückenvermerken und einer
festen Kennung je Zeile), Terminrahmen, Hinweisen und Zusammenfassung. Über
denselben Knopf wie beim Upload lässt es sich wieder öffnen, und das Audio
lässt sich erneut transkribieren. Der Server hält davon nichts; die Datei liegt
beim Nutzer. Das Format trägt eine Versionsnummer, eine neuere Datei wird mit
Hinweis abgelehnt. Solange eine Aufnahme läuft oder etwas ungesichert ist,
fragt der Browser vor dem Schließen nach.

Nur der **lokale whisper-server** nimmt keine ganzen Dateien. Für ihn wird die
Datei wie bisher im Browser dekodiert (`decodeAudioData` kann mp3, m4a, wav,
ogg, flac), auf 16 kHz Mono umgerechnet und durch dieselbe Leitung geschickt
wie das Mikrofon.

Ein **Strom-Dienst kommt dafür nicht in Frage**: Er transkribiert, was in
Echtzeit hineinläuft. Steht er eingestellt, weicht die Anwendung selbsttätig
auf den ersten dateifähigen Dienst aus.

## Sitzungsdaten

Am eigenen Rechner schreibt eine Sitzung laufend in ihr Verzeichnis unter
`SESSIONS_DIR` (Vorgabe `~/.local/share/live-protocol/sessions/<id>/`, in der
Desktop-Fassung im Datenordner des Systems): `audio.raw`, `transcript.md`,
`protocol.md`, bei der Delta-Strategie zusätzlich `state.json`. Bewusst
zustandsbehaftet — eine einstündige Besprechung darf nicht an einem
Browser-Absturz verloren gehen. Im Server-Betrieb bleiben Transkripte
dagegen nur im Arbeitsspeicher und fallen nach `SESSION_TTL_MINUTES` heraus.

## Warum kein Stapelbetrieb

Das Werkzeug ist auf den **Live-Fall** zugeschnitten: lokal, sitzungsbehaftet,
mit laufender Fortschreibung. Der Stapelfall — Datei hochladen, warten,
Ergebnis abholen — ist bewusst nicht enthalten, weil die Zustandsmodelle
gegensätzlich sind: dort ist „die App hält keine Daten" eine tragende
Eigenschaft (und macht Zugangsschutz entbehrlich), hier wäre sie falsch, denn
eine einstündige Aufzeichnung darf nicht an einem Browser-Absturz verloren
gehen.

## Bekannte Grenzen

- **Sprecher nur nachträglich und nur mit Voxtral.** Unterschieden werden sie
  nach dem Beenden über die ganze Aufnahme, bis drei Stunden am Stück. Die
  Aufnahme geht dafür ein zweites Mal an Mistral (rund 18 ct je Stunde).
- **Systemton nur über die Bildschirmfreigabe.** Er kommt per `getDisplayMedia`,
  also nur in Chromium-Browsern und unter macOS erst ab 14.2 mit Chrome 141.
  Mikrofon und Systemton landen als eine Mono-Spur in der Aufnahme. Ohne
  Kopfhörer nimmt das Mikrofon die Gegenseite zusätzlich auf. Das Desktop-Fenster
  ist ein Kindprozess des Bündels: macOS rechnet ihm die Bildschirmaufnahme zu,
  und weil das Bündel nicht signiert ist, verlangt jede neue Version die
  Erlaubnis noch einmal.
- **Beim gestückelten Weg ist Latenz strukturell.** Der erste Text kann
  frühestens nach der Mindest-Segmentlänge erscheinen (10 s). Kürzer stellen
  geht nicht sinnvoll: Whisper verarbeitet immer ein 30-Sekunden-Fenster. Der
  Strom-Dienst hat diese Grenze nicht.
- **Die Vorauswahl hängt an der Betriebsart.** Auf dem eigenen Rechner ist
  „Whisper (lokal)" vorausgewählt - das Audio bleibt dort, solange niemand
  etwas anderes wählt. Im Server-Betrieb gewinnt der erste eingerichtete
  Dienst: ein whisper.cpp-Server (`WHISPER_URL`), sonst die Transkription des
  OpenAI-kompatiblen Anbieters, sonst Voxtral Realtime.
- **Am Telefon nur mit wachem Bildschirm.** Kein Browser hält das Mikrofon
  an, wenn der Bildschirm gesperrt oder eine andere App vorne ist. Die
  Anwendung hält den Bildschirm wach und sagt es, wenn doch etwas fehlt. Der
  robustere Weg: mit der Sprachmemo-App aufnehmen und die Datei hochladen.

