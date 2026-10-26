# Overlay: eigene Dateien einer Instanz

Wer Live-Protokoll für andere betreibt, braucht oft Dinge, die nicht in den
Kern gehören: ein Impressum, eine Datenschutzerklärung, einen Hinweis oben,
ein eigenes Logo. Dafür gibt es das Overlay – ein Verzeichnis, das neben dem
Programm liegt und beim Ausliefern Vorrang hat. Der Kern bleibt unverändert
und lässt sich ohne Zusammenführen aktualisieren.

```bash
OVERLAY_DIR=/pfad/zum/overlay
```

Ohne `OVERLAY_DIR` (Vorgabe) verhält sich die Anwendung wie ausgeliefert.

## Was das Overlay kann

| Datei im Overlay | Wirkung |
|---|---|
| `<name>.html`, etwa `impressum.html`, `datenschutz.html` | wird unter `/<name>` erreichbar und bekommt einen Link in der Fußzeile |
| `banner.html` | Hinweisleiste oben, darf Links und Auszeichnung enthalten |
| `index.html` | ersetzt die Startseite (selten nötig, wird bei Updates nicht nachgezogen) |
| jede andere Datei, etwa `style.css`, `icon.svg`, `logo.png` | wird unter `/static/…` ausgeliefert, vor der gleichnamigen Datei aus `static/` |

Einzelheiten:

- **Seitennamen** bestehen aus Kleinbuchstaben, Ziffern und Bindestrichen
  (`impressum`, `nutzungs-bedingungen`). Andere Dateinamen werden nicht als
  Seite angeboten.
- **Beschriftung in der Fußzeile** ist der `<title>` der Seite bis zum ersten
  Gedankenstrich: Aus `<title>Datenschutz – Meine Instanz</title>` wird
  „Datenschutz“. Ohne `<title>` gilt der Dateiname. Die Links stehen
  alphabetisch nach Dateinamen.
- **Ohne Anmeldung erreichbar** sind die Seiten wie alle statischen Dateien
  auch. Rechtliche Angaben müssen für jeden abrufbar sein.
- **Gestaltung:** Die Seiten können das Stylesheet der Anwendung mitbenutzen
  (`<link rel="stylesheet" href="/static/style.css">`).
- **Hinweisleiste:** Statt `banner.html` geht auch `BANNER_TEXT=…` in der
  `.env`, dann als Klartext ohne Auszeichnung. Liegt beides vor, gewinnt die
  Datei. Weggeklickt bleibt die Leiste auf dem Gerät weg, bis sich ihr Text
  ändert.
- **Änderungen** wirken sofort, ohne Neustart: Das Verzeichnis wird bei jeder
  Anfrage gelesen.

Das Overlay ist für Inhalte und Aussehen gedacht, nicht für Programmlogik.
Wer `app.js` oder `index.html` ersetzt, muss diese Dateien bei jedem Update
selbst nachziehen.

## Hinweis vor der Aufnahme

Vor jeder Aufnahme fragt die Anwendung, ob alle Anwesenden einverstanden
sind (in Deutschland ist das Aufnehmen nicht öffentlich gesprochener Worte
ohne Zustimmung nach § 201 StGB strafbar). Wer den Hinweis anklickt, kann ihn
auf seinem Gerät ausblenden. Eine Instanz, die das anders regelt, schaltet
ihn mit `CONSENT_HINT=false` ganz ab.

## Beispiel mit Docker

```yaml
# compose.yaml
services:
  live-protocol:
    volumes:
      - daten:/data
      - ./overlay:/overlay:ro
```

```bash
# .env
OVERLAY_DIR=/overlay
```

```
overlay/
├── banner.html        Am Samstag ab 20 Uhr Wartung. <a href="/impressum">Kontakt</a>
├── datenschutz.html
└── impressum.html
```

Das Verzeichnis `overlay/` im Projektordner steht in `.gitignore` und landet
damit nicht versehentlich in einem Commit.
