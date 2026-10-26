"""Objektive Messung der Gewichtung: Wieviel von jedem Gesprächsabschnitt
überlebt im Endprotokoll?

Idee: Pro Zyklus die kennzeichnenden Begriffe bestimmen (häufig in diesem
Zyklus, selten in den anderen) und dann zählen, wieviele davon im
Endprotokoll auftauchen. Fällt die Abdeckung über die Zyklen ab, dominiert
der Gesprächsanfang - genau der Effekt, um den es geht.
"""

from __future__ import annotations

import re
from collections import Counter

STOPWORDS = set("""
aber alle allem allen aller alles als also andere anderen anders auch auf aus
bei beim bin bis bist da damit dann das dass dem den denn der deren des dessen
deshalb die dies diese diesem diesen dieser dieses doch dort du durch ein eine
einem einen einer eines einfach er erst es etwas euch für für gab gar gegen
geht gehen gemacht gerade gewesen gibt ganz habe haben hat hatte hatten hier
hin ich ihm ihn ihnen ihr ihre ihrem ihren ihrer immer indem ins ist ja jede
jedem jeden jeder jedes jetzt kann kannst kein keine keinen können können
konnte machen macht mal man mehr mein meine mit muss müssen müssen nach nicht
nichts noch nun nur ob oder ohne schon sehr sein seine seinem seinen seiner
seit selbst sich sie sind so solche soll sollen sondern sonst über über um und
uns unser unsere unter viel viele vom von vor wäre wäre war waren warum was
weg weil weiter welche wenn wer werde werden wie wieder wir wird wirklich wo
wollen wollte würde würde zu zum zur zwar zwischen etwa dabei dazu darauf
worden denen mich mir dich dir ihnen sagen sagt gesagt heute schon eigentlich
natürlich natürlich vielleicht wirklich einmal nochmal total völlig völlig
""".split())

_TOKEN_RE = re.compile(r"[a-zA-ZäöüÄÖÜß]{4,}")
# Deutsche Substantive sind großgeschrieben - ein billiger, überraschend
# wirksamer Filter gegen Füllwörter. Ohne ihn landen "naja", "glaube" und
# "will" unter den kennzeichnenden Begriffen und die Messung wird wertlos.
_NOUN_RE = re.compile(r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ]{4,}")

# Großgeschriebene Wörter, die keine inhaltlichen Substantive sind
# (Satzanfänge, Anreden).
_CAPITALISED_NOISE = {
    "aber", "also", "dann", "diese", "dieser", "dieses", "durch", "einer",
    "eine", "einen", "gerade", "haben", "heute", "ihnen", "insofern", "jetzt",
    "kommt", "können", "muss", "müssen", "nein", "nicht", "sondern", "wenn",
    "wieder", "wirklich", "würde", "sagen", "sehen", "glaube", "meine",
    "natürlich", "vielleicht", "warum", "weil", "wollen", "danke", "herr",
    "frau", "naja", "sagt", "will", "nehmen", "sprechen",
}


def tokens(text: str) -> list[str]:
    """Kennzeichnende Begriffe = großgeschriebene Wörter (deutsche Substantive)."""
    out = []
    for match in _NOUN_RE.findall(text):
        low = match.lower()
        if low in STOPWORDS or low in _CAPITALISED_NOISE:
            continue
        out.append(low)
    return out


def distinctive_terms(cycle_texts: list[str], top_k: int = 15, min_count: int = 2) -> list[list[str]]:
    """Pro Zyklus die kennzeichnenden Begriffe (häufig hier, selten anderswo)."""
    counters = [Counter(tokens(t)) for t in cycle_texts]
    totals = Counter()
    for c in counters:
        totals.update(c)

    result: list[list[str]] = []
    for c in counters:
        scored = []
        for term, count in c.items():
            if count < min_count:
                continue
            elsewhere = totals[term] - count
            scored.append((count / (1 + elsewhere), count, term))
        scored.sort(reverse=True)
        result.append([term for _, _, term in scored[:top_k]])
    return result


def coverage(terms: list[str], protocol: str) -> float:
    """Anteil der Begriffe, die im Protokoll vorkommen (Teilstring, kleingeschrieben)."""
    if not terms:
        return 0.0
    hay = protocol.lower()
    return sum(1 for t in terms if t in hay) / len(terms)


def coverage_by_cycle(cycle_texts: list[str], protocol: str, top_k: int = 15) -> list[float]:
    return [coverage(terms, protocol) for terms in distinctive_terms(cycle_texts, top_k=top_k)]
