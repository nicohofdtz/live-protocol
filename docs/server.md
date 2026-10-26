# Server-Betrieb

Ergänzung zur README: was sich im Server-Betrieb (`DEPLOYMENT_MODE=hosted`)
anders verhält und wie er eingerichtet wird.

## Zwei Betriebsarten

`DEPLOYMENT_MODE` entscheidet, wie sich dieselbe Anwendung verhält:

| | `desktop` | `hosted` |
|---|---|---|
| Schlüssel | in den Einstellungen (Zahnrad), lokal gespeichert | aus der `.env` |
| Transkription | Whisper lokal **oder** Cloud | Cloud, whisper.cpp-Server nur mit `WHISPER_URL` |
| Vorauswahl | Whisper (lokal) | erster eingerichteter Dienst |
| Sitzungen | zusätzlich auf der Platte (Absturzsicherheit) | nur im Arbeitsspeicher |
| Speichern | Projektdatei (ZIP) | dazu „Meine Sitzungen“, im Browser verschlüsselt |
| Grenzen | keine | Sitzungsdauer (`MAX_SESSION_MINUTES`) |
| Hinweis auf lokale Fassung | nein | ja, ausblendbar (`DOWNLOAD_URL`) |
| Update-Prüfung | ja (`UPDATE_CHECK`) | nein |

Benutzer gibt es in beiden Betriebsarten gleich: ohne Proxy einen einzigen,
impliziten – keine Anmeldung, keine Verbrauchsgrenze –, mit `AUTH_PROXY`
beliebig viele (siehe unten).

## Einrichten

```bash
cp .env.example .env      # MISTRAL_API_KEY oder OPENAI_COMPAT_* eintragen
docker compose up         # dann http://localhost:8000
```

Mehr braucht es nicht. Ohne Docker: `DEPLOYMENT_MODE=hosted` in die `.env`
und `uv run uvicorn app.main:app --port 8000` (lauscht wie der Container nur
auf `127.0.0.1`).

**HTTPS:** Browser geben das Mikrofon nur auf HTTPS-Seiten oder `localhost`
frei. Von einem anderen Gerät aus braucht es deshalb einen Reverse Proxy mit
TLS davor (etwa Caddy, nginx oder Traefik). WebSockets muss er durchreichen;
die meisten tun das ohne gesonderte Einstellung.

**Schutz:** Ohne `AUTH_PROXY` fragt die Anwendung niemanden nach einem
Namen. Wer sie erreicht, nutzt die Schlüssel aus der `.env`. Offen ins
Internet gehört sie deshalb nur mit einer Anmeldung davor – sei es ein Proxy
mit Anmeldung, ein VPN oder `AUTH_PROXY` (nächster Abschnitt). Beim Start
ohne Proxy steht eine Warnung im Log.

Deshalb ist der Port in `compose.yaml` nur auf diesem Rechner erreichbar
(`127.0.0.1:8000:8000`). Ein Reverse Proxy auf demselben Rechner kommt so
daran. Läuft der Proxy auf einem anderen Rechner, in `compose.yaml`
`"8000:8000"` eintragen und den Port per Firewall auf den Proxy beschränken – mit
`AUTH_PROXY` ist das ohnehin Pflicht.

Eigene Seiten wie Impressum und Datenschutzerklärung, eine Hinweisleiste oder
ein eigenes Logo kommen über das Overlay, siehe [overlay.md](overlay.md).

## Lokale Transkription auf dem Server

Auf dem Server steht die lokale Transkription nur zur Wahl, wenn ein
whisper.cpp-Server ausdrücklich eingerichtet ist: `WHISPER_URL` zeigt auf
dessen Adresse (`whisper-server`, Pfad `/inference`). Ist er eingerichtet,
ist er vorausgewählt.

```bash
docker compose --profile whisper up
# in der .env:
WHISPER_URL=http://whisper:8081
```

Das Profil startet das offizielle Abbild `ghcr.io/ggml-org/whisper.cpp:main`
und lädt beim ersten Start das Modell `WHISPER_MODEL` (Vorgabe
`large-v3-turbo-q5_0`) in ein eigenes Volume. Ohne GPU läuft large-v3-turbo
auf vielen Servern langsamer als Echtzeit; dann ein kleineres Modell wählen
(`WHISPER_MODEL=small`) oder einen vorhandenen whisper-server auf einem
stärkeren Rechner per `WHISPER_URL` eintragen.

Dasselbe gilt für Sprachmodelle im eigenen Netz: Ein OpenAI-kompatibler
Anbieter mit der Vorlage `ollama` oder einer Adresse im eigenen Netz steht
auch auf dem Server zur Wahl, ohne Schlüssel. Aus dem Container ist der
Rechner selbst unter `http://host.docker.internal` erreichbar.

## Mehrere Benutzer hinter einem Proxy

Hinter einem Proxy, der `Remote-User` setzt (z. B. Authelia oder
oauth2-proxy), mit `AUTH_PROXY=true`: Der Proxy prüft jede Anfrage, auch den
WebSocket-Handshake, und reicht `Remote-User` und `Remote-Groups` durch.
Fehlt die Kopfzeile, bleibt die Anwendung zu. Nur einschalten, wenn der Port
ausschließlich vom Proxy erreichbar ist – sonst ließe sich die Kopfzeile
fälschen.

Erst in diesem Modus gibt es:

- **Freigaben:** Ein Eintrag mit `"restricted": true` in
  `TRANSCRIPTION_ENGINES`, `LLM_ENDPOINTS` oder `SUMMARY_MODELS` erscheint nur
  für die Gruppe `lp-<id>` (bei `LLM_ENDPOINTS` `lp-<name>`); `lp-alle`
  schaltet alles frei. Gedacht für Dienste, die nach Verbrauch abrechnen.
  **Die Prüfung sitzt im Server**, nicht in der Oberfläche: Wer die Kennung
  errät und von Hand eine Sitzung anlegt, bekommt `400 Unbekannte Engine`.
- **Verbrauch:** Jeder bezahlte Aufruf schreibt eine Zeile nach
  `USAGE_DIR/<JJJJ-MM>.jsonl`: Benutzer, Dienst, Audiosekunden, Tokens,
  geschätzte Kosten aus den Listenpreisen der Konfiguration. Keine Inhalte.
  Zeilen älter als 13 Monate werden gelöscht.
- **Gleichzeitige Aufnahmen:** höchstens `MAX_SESSIONS_PER_USER` je Benutzer
  (Vorgabe 2). Der implizite Benutzer ohne Proxy hat keine Höchstzahl – er
  nimmt vielleicht am Telefon auf, während am Rechner noch eine Sitzung läuft.
- **Monatsgrenze:** `USAGE_LIMIT_USD` je Benutzer, die Gruppe
  `lp-limit-<betrag>` setzt eine eigene, die Gruppe `admins` hat keine.
- **Übersicht:** Wer in der Gruppe `admins` ist, sieht in der Fußzeile
  „Verbrauch“ die Monatssummen je Benutzer.

## Meine Sitzungen

Am Telefon aufnehmen, am Rechner weitermachen: Der Stand samt Audio lässt sich
auf dem Server ablegen – verschlüsselt im Browser, bevor er hochgeht. Das
gilt für den impliziten Benutzer genauso wie für Benutzer hinter dem Proxy.
Aus dem Tresor-Passwort entsteht per PBKDF2 (600.000 Runden) ein Schlüssel,
der einen zufälligen AES-256-Hauptschlüssel einwickelt; nur der eingewickelte
liegt auf dem Server. Wer den Server betreibt, sieht Größen und Zeitpunkte,
sonst nichts. Ein vergessenes Passwort lässt sich nicht zurücksetzen – nur
der Tresor leeren. „Auf diesem Gerät merken“ legt den Hauptschlüssel als
nicht exportierbaren Schlüssel in IndexedDB ab. Ist der Tresor offen, sichert
die Anwendung nach jedem Arbeitsschritt selbst; das Audio geht dabei nur
einmal hoch.

## Einstellungen je Benutzer

Was ein Benutzer im Zahnrad einstellt, liegt unter `USER_PREFS_DIR` als eine
JSON-Datei je Benutzer — damit die Auswahl am Telefon und am Rechner dieselbe
ist. Gespeichert werden nur die bekannten Felder; alles andere fällt weg.
Sitzungsinhalte sind davon nicht berührt, die bleiben im Arbeitsspeicher.
Unter „Meine Daten“ in den Einstellungen lädt jeder herunter, was der Server
über ihn weiß, oder löscht Einstellungen, Glossare und gespeicherte
Sitzungen.
