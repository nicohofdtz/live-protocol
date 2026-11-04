"""Screenshots für das README, automatisch aus einer laufenden Instanz.

Statt des Mikrofons bekommt die App eine WAV-Datei als Audiospur: getUserMedia
wird im Browser ersetzt. Die App nimmt also eine echte Besprechung auf - nur
eben eine synthetische. (Chromiums eigenes Fake-Gerät hängt unter macOS
headless in getUserMedia fest.)

    ./make_audio.sh                       # einmal: out/*.wav
    uv run --no-project --with playwright python shoot.py http://127.0.0.1:8765

Die Instanz braucht einen Mistral-Schlüssel (Voxtral Realtime, Sprecher,
Zusammenfassung). Bilder landen in docs/bilder/.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from playwright.sync_api import Page, TimeoutError, sync_playwright

HIER = Path(__file__).resolve().parent
BILDER = HIER.parent.parent / "docs" / "bilder"
GROESSE = {"width": 1440, "height": 900}

# Verhörte Produktnamen, wie Voxtral sie schreibt.
NAMEN = [(r"\b(?:Z[Aa][Mm]+[Aa][Tt]|Zamad|Sam+ad|Zam+ert|Zermatt|Zarmat+|Samat|verzahmt)\b", "Zammad"),
         (r"\b(?!Freshdesk)(?:Frisch|Fresh|Frech|Freis|Frees)[a-z]*[dt]?[ie]s[kt]\b", "Freshdesk"),
         (r"Frisch ist", "Freshdesk"), (r"\bKontendaten\b", "Kundendaten"),
         (r"\bTextvorschläge\b", "Textbausteine")]

# Spielt /__demo.wav ab und reicht den Ton als Mikrofon-Stream weiter.
# window.__demoFertig wird true, wenn die Datei durchgelaufen ist.
FAKE_MIKROFON = """
navigator.mediaDevices.getUserMedia = async () => {
  const ctx = new AudioContext();
  const daten = await (await fetch('/__demo.wav')).arrayBuffer();
  const quelle = ctx.createBufferSource();
  quelle.buffer = await ctx.decodeAudioData(daten);
  const ziel = ctx.createMediaStreamDestination();
  quelle.connect(ziel);
  quelle.onended = () => { window.__demoFertig = true; };
  quelle.start();
  return ziel.stream;
};
"""


def browser(p, wav: Path, profil: str):
    ctx = p.chromium.launch_persistent_context(
        profil, viewport=GROESSE, device_scale_factor=2, locale="de-DE",
        color_scheme="light", args=["--autoplay-policy=no-user-gesture-required"])
    daten = wav.read_bytes()
    ctx.route("**/__demo.wav", lambda r: r.fulfill(body=daten, content_type="audio/wav"))
    ctx.add_init_script(FAKE_MIKROFON)
    return ctx


def foto(page: Page, name: str) -> None:
    BILDER.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(BILDER / f"{name}.png"))
    print("  ", name)


def einrichten(page: Page) -> None:
    """Erster Start: Neuigkeiten und Modell-Download wegklicken."""
    # Beide erscheinen erst nach dem Laden der Konfiguration, in wechselnder Folge.
    for _ in range(20):
        for knopf in ("#first-run-skip", "#news-dialog button.primary"):
            if page.locator(knopf).is_visible():
                page.click(knopf)
        page.wait_for_timeout(250)


def einstellungen(page: Page, engine: str, *, live: bool, sprecher: bool = True) -> None:
    page.click("#settings-open")
    page.select_option("#set-engine", engine)
    page.set_checked("#set-live", live)
    page.set_checked("#set-speakers-after", sprecher)
    page.wait_for_timeout(400)
    page.click("#settings-save")
    page.wait_for_timeout(400)


def einstellungen_zeigen(page: Page, engine: str, bild: str) -> None:
    """Bild der Einstellungen, ohne etwas zu übernehmen."""
    page.set_viewport_size({"width": 1440, "height": 1240})
    page.click("#settings-open")
    page.select_option("#set-engine", engine)
    page.evaluate("document.activeElement.blur()")
    page.wait_for_timeout(400)
    foto(page, bild)
    page.click("#settings button[value=cancel]")
    page.set_viewport_size(GROESSE)
    page.wait_for_timeout(300)


def korrigieren(page: Page, muster: str, richtig: str, bild: str | None = None) -> bool:
    """Markiert ein verhörtes Wort im Transkript und korrigiert es wie ein Nutzer."""
    gefunden = page.evaluate("""(muster) => {
      const treffer = new RegExp(muster);
      const gang = document.createTreeWalker(document.getElementById('transcript'), NodeFilter.SHOW_TEXT);
      while (gang.nextNode()) {
        const m = gang.currentNode.textContent.match(treffer);
        if (!m) continue;
        const r = document.createRange();
        r.setStart(gang.currentNode, m.index);
        r.setEnd(gang.currentNode, m.index + m[0].length);
        const s = document.getSelection();
        s.removeAllRanges();
        s.addRange(r);
        return m[0];
      }
      return null;
    }""", muster)
    if not gefunden:
        print("   kein Treffer für", muster)
        return False
    print(f"   korrigiere {gefunden} -> {richtig}")
    page.wait_for_selector("#correct-word:not([hidden])")
    page.dispatch_event("#correct-word", "mousedown")
    page.locator("#correct-dialog").wait_for(state="visible")
    page.fill("#correct-text", richtig)
    page.wait_for_timeout(300)
    if bild:
        foto(page, bild)
    page.click("#correct-dialog button[value=save]")
    page.wait_for_timeout(300)
    return True


def aufnehmen(page: Page, *, einverstaendnis: bool = False,
              waehrend=None, zwischenbild: tuple[int, str] | None = None) -> None:
    """Nimmt die Demo-Datei ganz auf und beendet danach die Aufnahme."""
    page.click("#toggle")
    dialog = page.locator("#consent-dialog")
    dialog.wait_for(state="visible")
    page.wait_for_timeout(300)
    if einverstaendnis:
        foto(page, "einverstaendnis")
    dialog.locator("button[value=ok]").click()
    page.wait_for_selector("#stop:not([disabled])", timeout=30_000)
    if waehrend:
        waehrend(page)
    if zwischenbild:
        nach, name = zwischenbild
        page.wait_for_timeout(nach * 1000)
        try:   # Bild erst, wenn der erste Protokolllauf da ist
            page.wait_for_function(
                "!document.getElementById('protocol').innerText.includes('Wartet')",
                timeout=35_000, polling=500)
        except TimeoutError:
            print("   Protokolllauf kam nicht rechtzeitig")
        foto(page, name)
    page.wait_for_function("window.__demoFertig === true", timeout=600_000, polling=1000)
    page.wait_for_timeout(3000)   # letzte Sätze noch transkribieren lassen
    page.click("#stop")


def sprecher_benennen(page: Page) -> None:
    """Ordnet die erkannten Sprecher anhand ihrer Sätze den Namen zu."""
    page.locator("#speaker-dialog").wait_for(state="visible", timeout=120_000)
    reiter = page.locator("#speaker-tabs button")
    print("   Sprecher erkannt:", reiter.count())
    for i in range(reiter.count()):
        reiter.nth(i).click()
        page.wait_for_timeout(200)
        text = page.inner_text("#speaker-samples")
        if "Guten Morgen" in text or "halten wir fest" in text:
            name = "Julia"
        elif "zählt vor allem" in text or "sammle" in text or "klar für" in text:
            name = "Jonas"
        else:
            name = "Tobias"
        page.fill("#speaker-name", name)
    page.wait_for_timeout(300)
    foto(page, "sprecher")
    page.click("#speaker-dialog button[value=check]")
    page.wait_for_timeout(500)
    # Die Sprechererkennung transkribiert neu, ohne die Korrekturen von vorhin.
    for muster, richtig in NAMEN:
        while korrigieren(page, muster, richtig):
            pass
    page.click("#speakers-open")
    page.locator("#speaker-dialog").wait_for(state="visible")
    page.click("#speaker-dialog button[value=summary]")


def zusammenfassen(page: Page, bild: str | None = None) -> None:
    page.locator("#summary-dialog").wait_for(state="visible", timeout=120_000)
    page.wait_for_timeout(400)
    if bild:
        foto(page, bild)
    page.click("#summary-dialog button[value=run]")
    page.wait_for_timeout(1000)
    page.wait_for_function(
        "document.getElementById('cycle-badge').hidden"
        " && !document.getElementById('status').textContent.includes('…')",
        timeout=240_000, polling=1000)
    page.wait_for_timeout(1000)
    print("   Status:", page.inner_text("#status"))


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
    with sync_playwright() as p, tempfile.TemporaryDirectory() as profil:
        ctx = browser(p, HIER / "out" / "besprechung.wav", profil)
        page = ctx.pages[0]
        page.goto(url)
        einrichten(page)
        page.check("#output-type-meeting", force=True)
        einstellungen_zeigen(page, "whisper-local", "einstellungen")
        einstellungen(page, "voxtral-realtime", live=True)

        def verhoerte_namen(page: Page) -> None:
            page.wait_for_timeout(35_000)
            korrigieren(page, NAMEN[0][0], NAMEN[0][1], "korrektur")
            korrigieren(page, NAMEN[1][0], NAMEN[1][1])
            for _ in range(2):
                page.wait_for_timeout(15_000)
                for muster, richtig in NAMEN:
                    korrigieren(page, muster, richtig)

        aufnehmen(page, einverstaendnis=True, waehrend=verhoerte_namen,
                  zwischenbild=(15, "aufnahme-live"))
        sprecher_benennen(page)
        zusammenfassen(page, "zusammenfassung-optionen")
        page.set_viewport_size({"width": 1440, "height": 1240})
        page.evaluate("document.getElementById('transcript').scrollTop = 0")
        page.wait_for_timeout(500)
        foto(page, "protokoll")
        page.set_viewport_size(GROESSE)
        page.click("#export")
        page.wait_for_timeout(300)
        foto(page, "export")
        ctx.close()

        ctx = browser(p, HIER / "out" / "brainstorming.wav", profil)
        page = ctx.pages[0]
        page.goto(url)
        einrichten(page)
        page.check("#output-type-brainstorming", force=True)
        einstellungen(page, "voxtral-realtime", live=False, sprecher=False)
        aufnehmen(page)
        zusammenfassen(page)
        foto(page, "konzept")
        ctx.close()


if __name__ == "__main__":
    main()
