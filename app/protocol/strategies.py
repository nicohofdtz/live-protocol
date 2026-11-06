"""Die zwei Fortschreibungsstrategien.

rewrite  Das Modell bekommt Protokoll + neues Transkript und schreibt das
         Ganze neu. Der Veraltet-Hinweis im Systemprompt löst die Gewichtung.
delta    Das Modell bekommt eine Themenübersicht + neues Transkript und
         liefert nur Änderungsoperationen; gerendert wird in Python.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.llm import ChatClient, LLMError, extract_json, strip_code_fence
from app.output_types import OutputType
from app.protocol.state import ProtocolState

REWRITE_MECHANICS = """Du bist Protokollant einer laufenden Aufzeichnung und
führst ein Protokoll in deutscher Sprache als Markdown.

WICHTIG zur Gewichtung: Das bisherige Protokoll ist ein VERALTETER Stand. Es
entstand auf Basis eines früheren, kürzeren Ausschnitts und ist kein
Besitzstand. Deine Aufgabe ist nicht, es zu ergänzen, sondern es für den
GESAMTEN bisherigen Verlauf neu zu gewichten:

- Was sich später als Nebenschauplatz erwiesen hat, kürzt oder streichst du.
- Was den Hauptteil einnimmt, bekommt Raum, auch wenn es erst spät aufkam.
- Umstellen der Gliederung und Streichen ganzer Abschnitte sind erwünscht.
- Der Umfang eines Abschnitts soll dem Anteil entsprechen, den das Thema
  tatsächlich eingenommen hat.

Gib nur das Protokoll aus, keine Vorrede und keine Erklärungen."""

DELTA_MECHANICS = """Du pflegst ein strukturiertes Protokoll einer laufenden
Aufzeichnung.

Du bekommst (1) eine Übersicht der bereits erfassten Themen und (2) ein neues
Transkript-Segment. Du gibst AUSSCHLIESSLICH ein JSON-Objekt zurück, das die
nötigen Änderungen beschreibt. Du schreibst niemals das ganze Protokoll neu.

Format: {"ops": [ ... ]}

Erlaubte Operationen:
{"op":"add_topic","title":"<kurzer Titel>","summary":"<2-4 Sätze>"}
{"op":"extend_topic","id":"<vorhandene id>","add":"<1-3 Sätze>"}
{"op":"revise_topic","id":"<vorhandene id>","summary":"<neue Fassung>","title":"<optional>"}
{"op":"add_decision","text":"<Beschluss>","topic":"<id oder null>"}
{"op":"add_action","text":"<Aufgabe>","owner":"<Person oder null>","topic":"<id oder null>"}
{"op":"add_question","text":"<offene Frage>","topic":"<id oder null>"}

Regeln:
- Nur Operationen für das NEUE Segment. Nichts wiederholen, was laut
  Übersicht schon erfasst ist.
- Lieber ein Thema zu viel als eines zu wenig: Sobald ein neuer Aspekt
  eigenständig genug ist, um im Protokoll eine eigene Überschrift zu
  verdienen, legst du ihn als neues Thema an. extend_topic nur, wenn es
  wirklich derselbe Punkt ist.
- Ein Thema deckt einen Aspekt ab, nicht ein ganzes Gespräch. Bei einer
  breiten Diskussion sind fünf bis zehn Themen normal.
- Deutsch. Kein Text außerhalb des JSON-Objekts."""


POLISH_SYSTEM = """Du bekommst ein strukturiert gesammeltes Protokoll und
bringst es in seine Endfassung.

Erlaubt: Gliederung ordnen, verwandte Punkte zusammenfassen, Überschriften
schärfen, Wiederholungen streichen, Formulierungen glätten.
Nicht erlaubt: Inhalte hinzuerfinden oder inhaltliche Punkte weglassen.

Gib nur das fertige Protokoll als Markdown aus."""


@dataclass
class CycleResult:
    protocol: str
    endpoint: str
    prompt_tokens: int
    completion_tokens: int
    ops: int = 0


FEEDBACK_HEADER = """
Hinweise des Nutzers zur Protokollführung (dauerhaft beachten, sie beziehen
sich auf die Darstellung, nicht auf den Gesprächsinhalt):"""

REVISION_SYSTEM = """Du überarbeitest ein strukturiertes Protokoll auf Zuruf.

Du bekommst das vollständige Protokoll mit Themen-Kennungen und einen Hinweis
des Nutzers. Gib AUSSCHLIESSLICH ein JSON-Objekt {"ops": [...]} zurück, das den
Hinweis umsetzt.

Erlaubte Operationen:
{"op":"revise_topic","id":"<id>","summary":"<neue Fassung>","title":"<optional>"}
{"op":"add_topic","title":"<Titel>","summary":"<Text>"}

Ändere nur, was der Hinweis verlangt. Erfinde keine Inhalte hinzu."""


class Strategy(ABC):
    def __init__(self, client: ChatClient, output_type: OutputType):
        self.client = client
        self.output_type = output_type
        self.state = ProtocolState()
        self.protocol = ""
        # Merkt sich, ob der Abschlusslauf schon lief - Feedback danach muss
        # ihn wiederholen, sonst faellt das Protokoll auf die rohe Struktur
        # zurueck.
        self.polished = False
        # Steuernde Hinweise des Nutzers - gelten ab Eingang für jeden
        # weiteren Lauf, nicht nur für den nächsten.
        self.feedback: list[str] = []
        # Was vor dem Termin über ihn bekannt ist: Agenda, Beteiligte, Ziel.
        # Nachreichbar, gilt dann ab dem nächsten Lauf.
        self.context: dict[str, str] = {}

    def _context(self) -> str:
        return context_block(self.context)

    def _guidance(self) -> str:
        text = "\n\nInhaltliche Vorgaben:\n" + self.output_type.guidance
        text += self._context()
        if self.feedback:
            text += FEEDBACK_HEADER + "\n" + "\n".join(f"- {f}" for f in self.feedback)
        return text

    async def apply_feedback(self, text: str) -> str:
        """Nimmt einen Hinweis auf und setzt ihn um. Liefert das neue Protokoll."""
        self.feedback.append(text.strip())
        return self.protocol

    @abstractmethod
    async def update(self, segment: str, cycle: int) -> CycleResult: ...

    async def polish(self) -> str:
        """Einmaliger Abschlusslauf. Standard: nichts zu tun."""
        return self.protocol


def context_block(context: dict[str, str]) -> str:
    """Der Terminrahmen als Kontext, nicht als Auftrag.

    Bewusst so eingeleitet: Eine Agenda im Systemtext, die wie eine
    Anweisung klingt, verleitet das Modell dazu, Punkte zu protokollieren,
    die nie besprochen wurden.
    """
    felder = [
        ("Thema oder Anlass", context.get("topic")),
        ("Agenda", context.get("agenda")),
        ("Beteiligte", context.get("participants")),
        ("Ziel des Termins", context.get("goal")),
    ]
    gefuellt = [(name, str(wert).strip()) for name, wert in felder
                if wert and str(wert).strip()]
    if not gefuellt:
        return ""
    zeilen = "\n".join(f"{name}: {wert}" for name, wert in gefuellt)
    return (
        "\n\nZum Termin ist vorab Folgendes bekannt. Das ist Hintergrund, "
        "keine Vorgabe: Nimm nur auf, was tatsächlich gesagt wurde. Nutze es, "
        "um Themen einzuordnen, Namen und Abkürzungen richtig zu schreiben und "
        "die Gliederung an der Agenda auszurichten, soweit der Verlauf das "
        "hergibt.\n" + zeilen
    )


class RewriteStrategy(Strategy):
    async def update(self, segment: str, cycle: int) -> CycleResult:
        system = REWRITE_MECHANICS + self._guidance()
        user = (
            f"Bisheriges Protokoll:\n{self.protocol or '(noch keins)'}\n\n"
            f"Neues Transkript-Segment:\n{segment}\n\nAktualisiere das Protokoll."
        )
        completion = await self.client.complete(system=system, user=user)
        self.protocol = strip_code_fence(completion.text)
        return CycleResult(
            protocol=self.protocol,
            endpoint=completion.endpoint,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
        )

    async def apply_feedback(self, text: str) -> str:
        """Hinweis merken und sofort einarbeiten - ein Volldurchlauf kann das direkt."""
        self.feedback.append(text.strip())
        if not self.protocol:
            return self.protocol
        completion = await self.client.complete(
            system=REWRITE_MECHANICS + self._guidance(),
            user=f"Bisheriges Protokoll:\n{self.protocol}\n\n"
                 f"Hinweis des Nutzers:\n{text.strip()}\n\n"
                 "Überarbeite das Protokoll entsprechend. Keine Inhalte hinzuerfinden.",
        )
        if completion.text.strip():
            self.protocol = strip_code_fence(completion.text)
        return self.protocol


class DeltaStrategy(Strategy):
    async def update(self, segment: str, cycle: int) -> CycleResult:
        system = DELTA_MECHANICS + self._guidance()
        user = (
            f"Bereits erfasste Themen:\n{self.state.digest()}\n\n"
            f"Neues Transkript-Segment:\n{segment}\n\n"
            "Gib die nötigen Operationen als JSON zurück."
        )
        completion = await self.client.complete(system=system, user=user, json_mode=True)
        prompt_tokens = completion.prompt_tokens
        completion_tokens = completion.completion_tokens

        try:
            data = extract_json(completion.text)
        except LLMError:
            # Ein Reparaturversuch, bevor der Zyklus verloren geht.
            retry = await self.client.complete(
                system=system,
                user=user + '\n\nDeine letzte Antwort war kein gültiges JSON. '
                            'Antworte NUR mit {"ops": [...]}.',
                json_mode=True,
            )
            prompt_tokens += retry.prompt_tokens
            completion_tokens += retry.completion_tokens
            try:
                data = extract_json(retry.text)
            except LLMError:
                self.state.invalid_ops += 1
                return CycleResult(
                    protocol=self.state.render(), endpoint=completion.endpoint,
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                )

        # Manche Modelle liefern die Operationen als nackte Liste.
        ops = data.get("ops") if isinstance(data, dict) else data
        applied = self.state.apply(ops, cycle) if isinstance(ops, list) else 0
        if not isinstance(ops, list):
            self.state.invalid_ops += 1
        self.protocol = self.state.render()
        return CycleResult(
            protocol=self.protocol, endpoint=completion.endpoint,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, ops=applied,
        )


    async def polish(self) -> str:
        """Abschlusslauf über die gesammelte Struktur.

        Die Delta-Strategie ist während der Aufnahme unschlagbar schnell
        (konstant ~4 s statt wachsend bis 44 s), liefert aber eine
        sachlichere, weniger ausformulierte Gliederung. Ein einziger Lauf am
        Ende holt das auf, ohne die Laufzeit während der Sitzung zu belasten.
        """
        if not self.protocol:
            return self.protocol
        completion = await self.client.complete(
            system=POLISH_SYSTEM + self._guidance(),
            user=self.protocol,
        )
        if completion.text.strip():
            self.protocol = strip_code_fence(completion.text)
            self.polished = True
        return self.protocol

    async def apply_feedback(self, text: str) -> str:
        """Hinweis merken und einen Überarbeitungslauf über die Struktur fahren.

        Die Delta-Strategie sieht sonst nie das ganze Protokoll - genau das ist
        ihr Geschwindigkeitsvorteil. Für einen Nutzerhinweis muss sie es
        einmal sehen; danach geht es mit der billigen Übersicht weiter.
        """
        self.feedback.append(text.strip())
        if not self.state.topics:
            return self.protocol

        overview = "\n\n".join(
            f"[{t.id}] {t.title}\n{t.summary}" for t in self.state.topics
        )
        completion = await self.client.complete(
            system=REVISION_SYSTEM + self._guidance(),
            user=f"Protokoll:\n{overview}\n\nHinweis des Nutzers:\n{text.strip()}",
            json_mode=True,
        )
        try:
            data = extract_json(completion.text)
        except LLMError:
            return self.protocol
        ops = data.get("ops")
        if isinstance(ops, list):
            self.state.apply(ops, -1)
            self.protocol = self.state.render()
            if self.polished:
                # Nach dem Abschlusslauf darf Feedback nicht auf die rohe
                # Struktur zurueckfallen.
                self.polished = False
                await self.polish()
        return self.protocol


STRATEGIES = {"rewrite": RewriteStrategy, "delta": DeltaStrategy}


def create_strategy(name: str, client: ChatClient, output_type: OutputType) -> Strategy:
    try:
        cls = STRATEGIES[name]
    except KeyError:
        raise ValueError(f"Unbekannte Strategie: {name}") from None
    return cls(client, output_type)
