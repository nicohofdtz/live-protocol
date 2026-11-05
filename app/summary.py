"""Die Zusammenfassung nach dem Termin.

Ein Lauf über das ganze Transkript, mit einem besseren Modell als dem für das
Live-Protokoll. Ein Modul für alle Aufrufer: die Aufnahme, den Upload und
später ein wieder geöffnetes Projekt. Es hängt an keiner laufenden Sitzung -
der Browser schickt, was er hat, und bekommt Markdown zurück. So hält der
Server im gehosteten Betrieb nichts fest, und ein Projekt lässt sich
auch nach einem Neustart zusammenfassen.

Das Live-Protokoll geht bewusst nicht mit ein: Als Zusatzeingabe hat
es in der Praxis nichts gebracht, und seine Fehler sollen sich nicht in die
Endfassung fortpflanzen.

Gemessen (experiments/BEFUNDE.md): GLM 5.3 fasst drei Stunden Transkript in
einem Lauf, ohne die Mitte zu verlieren. Ein Zusammenfassen in Stufen gibt es
deshalb nicht.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from app.llm import ChatClient, LLMError, extract_json, strip_code_fence
from app.output_types import OutputType
from app.protocol.strategies import context_block

# Die Regeln, an denen die Qualität hängt. Ein Tester nannte ein Protokoll
# "abenteuerlich", weil es wilde Zusammenhänge knüpfte - daher die zweite
# Regel so ausdrücklich.
MECHANICS = """Du erhältst das vollständige Transkript eines Termins und \
schreibst daraus das endgültige Dokument, auf Deutsch, in Markdown. Es beginnt \
mit einer Überschrift (#), die den Inhalt in wenigen Worten nennt.

Regeln:
- Nur was im Transkript gesagt wurde. Nichts hinzuerfinden, nichts aus \
Allgemeinwissen ergänzen.
- Keine Zusammenhänge herstellen, die im Gespräch nicht ausdrücklich \
hergestellt wurden. Was nacheinander besprochen wurde, hängt nicht deshalb \
zusammen.
- Was unklar, widersprüchlich oder unverständlich geblieben ist, als unklar \
kennzeichnen, statt es zu glätten.
- Das Transkript stammt aus einer automatischen Spracherkennung und enthält \
Hörfehler. Offensichtlich falsch erkannte Wörter und Namen stillschweigend \
richtig schreiben, wenn der Zusammenhang eindeutig ist; sonst so stehen lassen. \
Das Dokument erwähnt weder das Transkript noch Hörfehler oder Schreibvarianten: \
Jeder Name steht nur in einer Schreibweise, ohne Klammerzusatz.
- Vermerke in eckigen Klammern wie [Aufnahme unterbrochen - …] bedeuten: Dort \
fehlt ein Stück. An dieser Stelle keinen Übergang erfinden. Ist die Lücke für \
das Verständnis wichtig, erwähne sie.
- Antworte nur mit dem Dokument, ohne Vorrede und ohne Codeblock."""

# Benannte Stufen statt einer Skala: Unter "7 von 10" versteht jedes Modell
# etwas anderes, eine Bauanweisung befolgt es.
DETAIL = {
    "knapp": "Umfang: knapp. Nur Ergebnisse, Entscheidungen und Aufgaben, je "
             "Thema wenige Stichpunkte. Kein Diskussionsverlauf.",
    "standard": "Umfang: Standard. Je Thema die Kernaussagen und das Ergebnis, "
                "dazu die Begründung, wenn sie genannt wurde.",
    "ausfuehrlich": "Umfang: ausführlich. Je Thema der Verlauf der Diskussion "
                    "mit den vorgebrachten Argumenten und Gegenargumenten, "
                    "danach das Ergebnis.",
}
DEFAULT_DETAIL = "standard"

# Nur mit benannten Sprechern. Hebt die Regel "keine Sprecherzuordnung
# erfinden" nicht auf: zugeordnet wird, was die Zeile sagt, nicht mehr.
PERSONEN = """Vor jeder Zeile des Transkripts steht, wer spricht. Ordne \
Aussagen, Positionen, Entscheidungen und Aufgaben diesen Personen zu. Zeilen \
mit „Sprecher“ und einer Nummer oder mit „mehrere Sprecher“ stammen von nicht \
benannten oder nicht getrennten Personen: Gib sie unpersönlich wieder und \
erfinde keinen Namen dafür. Steht bei „mehrere Sprecher“ ein Name mit \
„darunter“, spricht in diesen Zeilen nicht sicher diese Person - schreib \
solche Aussagen ihr nicht zu."""


@dataclass
class SummaryResult:
    markdown: str
    endpoint: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def format_transcript(entries: list[dict], personen: bool = False) -> str:
    """Eine Zeile je Eintrag mit Zeitmarke. Lückenvermerke bleiben stehen,
    wo sie sind - das Modell soll sehen, dass dort etwas fehlt.
    Mit `personen` steht der Sprecher davor; sonst gehen keine Namen mit."""
    zeilen = []
    for entry in entries:
        text = str(entry.get("text") or "").strip()
        if not text:
            continue
        sprecher = str(entry.get("speaker") or "").strip()
        wer = f"{sprecher}: " if personen and sprecher and not entry.get("gap") else ""
        zeilen.append(f"[{_mmss(float(entry.get('seconds') or 0))}] {wer}{text}")
    return "\n".join(zeilen)


def build_prompt(output_type: OutputType, entries: list[dict],
                 context: dict | None = None, detail: str = DEFAULT_DETAIL,
                 blocks: list[str] | None = None, freitext: str = "",
                 feedback: list[str] | None = None,
                 personen: bool = False, glossar: str = "") -> tuple[str, str]:
    """System- und Nutzertext für einen Lauf. Getrennt vom Aufruf, damit
    sich prüfen lässt, was beim Modell ankommt."""
    teile = [MECHANICS, "Inhaltliche Vorgaben:\n" + output_type.guidance,
             DETAIL.get(detail, DETAIL[DEFAULT_DETAIL])]

    gewaehlt = set(blocks) if blocks is not None else {
        b.id for b in output_type.summary_blocks if b.default}
    bausteine = [b.instruction for b in output_type.summary_blocks if b.id in gewaehlt]
    if bausteine:
        teile.append("Außerdem:\n" + "\n".join(f"- {b}" for b in bausteine))
    personen = personen and any(e.get("speaker") for e in entries)
    if personen:
        teile.append(PERSONEN)

    begriffe = [b.strip() for b in re.split(r"[,\n]", glossar or "") if b.strip()]
    if begriffe:
        # Die Begriffsliste des Nutzers: Die Erkennung schreibt Namen
        # oft falsch, hier steht, wie sie richtig heißen.
        teile.append("So werden Namen und Fachbegriffe richtig geschrieben – "
                     "ähnlich klingende Wörter im Transkript sind vermutlich diese: "
                     + ", ".join(begriffe[:200]))

    rahmen = context_block({k: str(v) for k, v in (context or {}).items()})
    if rahmen:
        teile.append(rahmen.strip())

    hinweise = [h.strip() for h in (feedback or []) if h and h.strip()]
    if hinweise:
        teile.append("Hinweise des Nutzers zur Machart, während des Termins "
                     "gesammelt - sie gelten für dieses Dokument:\n"
                     + "\n".join(f"- {h}" for h in hinweise))
    if freitext.strip():
        teile.append("Zusätzliche Vorgabe für dieses Dokument:\n" + freitext.strip())

    user = "Transkript:\n\n" + format_transcript(entries, personen)
    return "\n\n".join(teile), user


async def summarize(chat: ChatClient, output_type: OutputType, entries: list[dict],
                    **options) -> SummaryResult:
    system, user = build_prompt(output_type, entries, **options)
    beginn = time.monotonic()
    completion = await chat.complete(system=system, user=user)
    return SummaryResult(
        markdown=strip_code_fence(completion.text).strip(),
        endpoint=completion.endpoint,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        seconds=round(time.monotonic() - beginn, 1),
    )


# -- Anmerkungen einarbeiten -------------------------------------------

REVISE = """Du bekommst ein fertiges Protokoll in Markdown und Anmerkungen des \
Nutzers dazu. Arbeite jede Anmerkung ein - aber nicht, indem du das Protokoll \
neu schreibst, sondern mit möglichst kleinen Ersetzungen.

Antworte nur mit JSON in dieser Form:
{"ersetzungen": [{"alt": "…", "neu": "…", "alle": false}]}

Regeln:
- "alt" ist ein Ausschnitt, der zeichengenau so im Protokoll steht - so kurz \
wie möglich, aber lang genug, dass er eindeutig ist.
- "neu" ersetzt genau diesen Ausschnitt. Alles andere bleibt unverändert.
- Soll derselbe Ausschnitt überall ersetzt werden, etwa ein falsch \
geschriebener Name, gib ihn einmal an und setze "alle": true. Sonst gilt die \
Ersetzung nur für die erste Stelle.
- Soll etwas ergänzt werden, nimm als "alt" die Stelle, hinter der es steht, \
und wiederhole sie in "neu" mit der Ergänzung.
- Erfinde keine Inhalte. Verlangt eine Anmerkung etwas, das im Protokoll \
nicht belegt ist, setze nur um, was die Anmerkung selbst sagt."""


def _anmerkungen_text(anmerkungen: list[dict]) -> str:
    zeilen = []
    for nummer, a in enumerate(anmerkungen, 1):
        stelle = str(a.get("quote") or "").strip()
        kommentar = str(a.get("comment") or "").strip()
        if stelle:
            zeilen.append(f"{nummer}. Zur Stelle „{stelle}“: {kommentar}")
        else:
            zeilen.append(f"{nummer}. Allgemein: {kommentar}")
    return "\n".join(zeilen)


async def revise(chat: ChatClient, markdown: str, anmerkungen: list[dict]) -> dict:
    """Ersetzungen für die Anmerkungen holen. Eingesetzt werden sie im
    Browser: So bleibt der Rest des Protokolls garantiert, wie er ist, und
    jede Änderung lässt sich einzeln zurücknehmen."""
    beginn = time.monotonic()
    completion = await chat.complete(
        system=REVISE,
        user=f"Protokoll:\n\n{markdown}\n\nAnmerkungen:\n{_anmerkungen_text(anmerkungen)}",
        json_mode=True)
    try:
        daten = extract_json(completion.text)
    except LLMError as exc:
        raise LLMError(f"Die Antwort war kein gültiges JSON: {exc}") from exc
    ersetzungen = [
        {"alt": str(e["alt"]), "neu": str(e.get("neu") or ""), "alle": e.get("alle") is True}
        for e in daten.get("ersetzungen") or []
        # Eine Ersetzung, die nichts ändert, ist keine - sie stünde sonst als
        # Änderung in der Liste.
        if isinstance(e, dict) and e.get("alt") and str(e["alt"]) != str(e.get("neu") or "")
    ]
    return {"replacements": ersetzungen, "seconds": round(time.monotonic() - beginn, 1),
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens}
