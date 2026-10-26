"""Strukturierter Protokollzustand für die Delta-Strategie.

Das Modell liefert Operationen, angewendet wird deterministisch in Python.
Dadurch sieht das Modell nie das fertige Dokument - es hat also nichts, woran
es sich festhalten könnte, und der Prompt bleibt unabhängig von der
Gesprächslänge beschränkt.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Topic:
    id: str
    title: str
    summary: str
    first_cycle: int
    last_cycle: int


@dataclass
class Entry:
    text: str
    cycle: int
    topic: str | None = None
    owner: str | None = None


@dataclass
class ProtocolState:
    topics: list[Topic] = field(default_factory=list)
    decisions: list[Entry] = field(default_factory=list)
    actions: list[Entry] = field(default_factory=list)
    questions: list[Entry] = field(default_factory=list)
    invalid_ops: int = 0

    # -- Prompt-Eingabe -------------------------------------------------

    def digest(self) -> str:
        """Kompakte Übersicht für das Modell - Titel, nicht Volltext."""
        if not self.topics:
            return "(noch keine)"
        return "\n".join(
            f"{t.id} | {t.title} | {t.summary.split('.')[0].strip()}" for t in self.topics
        )

    # -- Anwenden -------------------------------------------------------

    def apply(self, ops: list, cycle: int) -> int:
        """Wendet Operationen an. Liefert die Anzahl der gültigen Operationen."""
        by_id = {t.id: t for t in self.topics}
        applied = 0
        for op in ops:
            if not isinstance(op, dict):
                self.invalid_ops += 1
                continue
            kind = op.get("op")
            if kind == "add_topic" and op.get("title"):
                topic = Topic(
                    id=f"t{len(self.topics) + 1}",
                    title=str(op["title"]),
                    summary=str(op.get("summary", "")),
                    first_cycle=cycle,
                    last_cycle=cycle,
                )
                self.topics.append(topic)
                by_id[topic.id] = topic
                applied += 1
            elif kind == "revise_topic" and op.get("summary"):
                # Überarbeitet ein bestehendes Thema vollständig. Nötig, damit
                # Nutzer-Feedback ("Punkt X kommt zu kurz") auch in der
                # Delta-Strategie umgesetzt werden kann - Anhängen allein
                # reicht dafür nicht.
                topic = by_id.get(str(op.get("id")))
                if topic is None:
                    self.invalid_ops += 1
                    continue
                topic.summary = str(op["summary"]).strip()
                if op.get("title"):
                    topic.title = str(op["title"])
                topic.last_cycle = cycle
                applied += 1
            elif kind == "extend_topic" and op.get("add"):
                topic = by_id.get(str(op.get("id")))
                if topic is None:
                    self.invalid_ops += 1
                    continue
                topic.summary = (topic.summary.rstrip() + " " + str(op["add"]).strip()).strip()
                topic.last_cycle = cycle
                applied += 1
            elif kind in ("add_decision", "add_action", "add_question") and op.get("text"):
                target = {
                    "add_decision": self.decisions,
                    "add_action": self.actions,
                    "add_question": self.questions,
                }[kind]
                target.append(
                    Entry(
                        text=str(op["text"]),
                        cycle=cycle,
                        topic=op.get("topic") if isinstance(op.get("topic"), str) else None,
                        owner=op.get("owner") if isinstance(op.get("owner"), str) else None,
                    )
                )
                applied += 1
            else:
                self.invalid_ops += 1
        return applied

    # -- Ausgabe --------------------------------------------------------

    def render(self) -> str:
        parts: list[str] = []
        for topic in self.topics:
            parts.append(f"## {topic.title}")
            if topic.summary:
                parts.append(topic.summary.strip())
            parts.append("")
        if self.decisions:
            parts.append("## Beschlüsse")
            parts += [f"- {d.text}" for d in self.decisions]
            parts.append("")
        if self.actions:
            parts.append("## Aufgaben")
            parts += [
                f"- {a.text}" + (f" ({a.owner})" if a.owner else "") for a in self.actions
            ]
            parts.append("")
        if self.questions:
            parts.append("## Offene Fragen")
            parts += [f"- {q.text}" for q in self.questions]
            parts.append("")
        return "\n".join(parts).strip()

    def to_dict(self) -> dict:
        return {
            "topics": [asdict(t) for t in self.topics],
            "decisions": [asdict(e) for e in self.decisions],
            "actions": [asdict(e) for e in self.actions],
            "questions": [asdict(e) for e in self.questions],
            "invalid_ops": self.invalid_ops,
        }
