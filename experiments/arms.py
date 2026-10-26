"""Die drei Fortschreibungsstrategien, die verglichen werden.

A  naiver Volldurchlauf        - bisheriges Protokoll + neues Transkript, "aktualisiere"
B  Volldurchlauf + Veraltet-Hinweis
C  Delta/Append in eine Struktur     - Vorschlag aus der Beratung
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from llm import chat, extract_json, strip_think

# ---------------------------------------------------------------- Arm A

SYSTEM_A = """Du bist Protokollant einer laufenden Diskussion. Du führst ein
Ergebnisprotokoll in deutscher Sprache als Markdown.

Gliedere nach Themen. Halte Kernaussagen, Positionen und strittige Punkte fest.
Gib nur das Protokoll aus, keine Vorrede und keine Erklärungen."""

USER_A = """Bisheriges Protokoll:
{protocol}

Neues Transkript-Segment:
{segment}

Aktualisiere das Protokoll."""

# ---------------------------------------------------------------- Arm B

SYSTEM_B = """Du bist Protokollant einer laufenden Diskussion. Du führst ein
Ergebnisprotokoll in deutscher Sprache als Markdown.

WICHTIG zur Gewichtung: Das bisherige Protokoll ist ein VERALTETER Stand. Es
wurde auf Basis eines früheren, kürzeren Ausschnitts des Gesprächs
geschrieben und ist ausdrücklich kein Besitzstand. Deine Aufgabe ist nicht,
es zu ergänzen, sondern das Protokoll für den GESAMTEN bisherigen
Gesprächsverlauf neu zu gewichten:

- Themen, die sich im weiteren Verlauf als Nebenschauplatz erwiesen haben,
  kürzt oder streichst du.
- Themen, die den Hauptteil des Gesprächs einnehmen, bekommen entsprechend
  Raum, auch wenn sie erst spät aufkamen.
- Umstellen der Gliederung und Streichen ganzer Abschnitte sind ausdrücklich
  erwünscht.
- Der Umfang eines Abschnitts soll ungefähr dem Anteil entsprechen, den das
  Thema im Gespräch tatsächlich eingenommen hat.

Gliedere nach Themen. Halte Kernaussagen, Positionen und strittige Punkte fest.
Gib nur das Protokoll aus, keine Vorrede und keine Erklärungen."""

USER_B = USER_A

# ---------------------------------------------------------------- Arm C

SYSTEM_C = """Du pflegst ein strukturiertes Protokoll einer laufenden Diskussion.

Du bekommst (1) eine Übersicht der bereits erfassten Themen und (2) ein neues
Transkript-Segment. Du gibst AUSSCHLIESSLICH ein JSON-Objekt zurück, das die
nötigen Änderungen beschreibt. Du schreibst niemals das ganze Protokoll neu.

Format:
{"ops": [ ... ]}

Erlaubte Operationen:
{"op":"add_topic","title":"<kurzer Titel>","summary":"<2-4 Sätze>"}
{"op":"extend_topic","id":"<vorhandene id>","add":"<1-3 Sätze Ergänzung>"}
{"op":"add_decision","text":"<Beschluss>","topic":"<id oder null>"}
{"op":"add_action","text":"<Aufgabe>","owner":"<Person oder null>","topic":"<id oder null>"}
{"op":"add_question","text":"<offene Frage>","topic":"<id oder null>"}

Regeln:
- Nur Operationen für das NEUE Segment. Nichts wiederholen, was laut Übersicht
  schon erfasst ist.
- Ein neues Thema nur, wenn es wirklich ein anderes Thema ist; sonst
  extend_topic auf das passende vorhandene.
- Deutsch. Keine Erklärungen, kein Text außerhalb des JSON-Objekts."""

USER_C = """Bereits erfasste Themen:
{digest}

Neues Transkript-Segment:
{segment}

Gib die nötigen Operationen als JSON zurück."""


@dataclass
class ProtocolState:
    """Strukturierter Protokollzustand für Arm C."""

    topics: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    invalid_ops: int = 0

    def digest(self) -> str:
        if not self.topics:
            return "(noch keine)"
        lines = []
        for t in self.topics:
            first = t["summary"].split(".")[0].strip()
            lines.append(f"{t['id']} | {t['title']} | {first}")
        return "\n".join(lines)

    def apply(self, ops: list[dict], cycle: int) -> None:
        by_id = {t["id"]: t for t in self.topics}
        for op in ops:
            if not isinstance(op, dict):
                self.invalid_ops += 1
                continue
            kind = op.get("op")
            if kind == "add_topic":
                title = op.get("title")
                if not title:
                    self.invalid_ops += 1
                    continue
                tid = f"t{len(self.topics) + 1}"
                topic = {
                    "id": tid,
                    "title": str(title),
                    "summary": str(op.get("summary", "")),
                    "first_cycle": cycle,
                    "last_cycle": cycle,
                }
                self.topics.append(topic)
                by_id[tid] = topic
            elif kind == "extend_topic":
                topic = by_id.get(str(op.get("id")))
                if topic is None or not op.get("add"):
                    self.invalid_ops += 1
                    continue
                topic["summary"] = topic["summary"].rstrip() + " " + str(op["add"]).strip()
                topic["last_cycle"] = cycle
            elif kind in ("add_decision", "add_action", "add_question"):
                text = op.get("text")
                if not text:
                    self.invalid_ops += 1
                    continue
                entry = {"text": str(text), "topic": op.get("topic"), "cycle": cycle}
                if kind == "add_action":
                    entry["owner"] = op.get("owner")
                target = {
                    "add_decision": self.decisions,
                    "add_action": self.actions,
                    "add_question": self.questions,
                }[kind]
                target.append(entry)
            else:
                self.invalid_ops += 1

    def render(self) -> str:
        parts = ["# Protokoll", ""]
        for t in self.topics:
            parts.append(f"## {t['title']}")
            parts.append(t["summary"].strip())
            parts.append("")
        if self.decisions:
            parts.append("## Beschlüsse")
            parts += [f"- {d['text']}" for d in self.decisions]
            parts.append("")
        if self.actions:
            parts.append("## Aufgaben")
            for a in self.actions:
                owner = f" ({a['owner']})" if a.get("owner") else ""
                parts.append(f"- {a['text']}{owner}")
            parts.append("")
        if self.questions:
            parts.append("## Offene Fragen")
            parts += [f"- {q['text']}" for q in self.questions]
            parts.append("")
        return "\n".join(parts).strip()


def run_rewrite_arm(system: str, protocol: str, segment: str, **kw) -> tuple[str, dict, float]:
    """Arm A/B: ein Volldurchlauf, Rückgabe ist das komplette neue Protokoll."""
    user = USER_A.format(protocol=protocol or "(noch keins)", segment=segment)
    content, usage, latency = chat(system=system, user=user, **kw)
    return strip_think(content), usage, latency


def run_delta_arm(state: ProtocolState, segment: str, cycle: int, **kw) -> tuple[dict, float, int]:
    """Arm C: Delta-Operationen anfordern und auf den Zustand anwenden.

    Liefert (usage, Latenz, Anzahl angewandter ops). Bei kaputtem JSON wird
    einmal mit verschärftem Hinweis nachgefordert (Repair-Retry).
    """
    user = USER_C.format(digest=state.digest(), segment=segment)
    content, usage, latency = chat(system=SYSTEM_C, user=user, json_mode=True, **kw)
    try:
        data = extract_json(content)
    except Exception:
        repair = user + "\n\nDeine letzte Antwort war kein gültiges JSON. Antworte NUR mit {\"ops\": [...]}."
        content, usage2, latency2 = chat(system=SYSTEM_C, user=repair, json_mode=True, **kw)
        latency += latency2
        for k, v in usage2.items():
            if isinstance(v, int):
                usage[k] = usage.get(k, 0) + v
        try:
            data = extract_json(content)
        except Exception:
            state.invalid_ops += 1
            return usage, latency, 0

    ops = data.get("ops")
    if not isinstance(ops, list):
        state.invalid_ops += 1
        return usage, latency, 0
    state.apply(ops, cycle)
    return usage, latency, len(ops)
