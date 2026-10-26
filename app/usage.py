"""Verbrauch je Benutzer: Audio-Minuten, Tokens, geschätzte Kosten.

Im gehosteten Betrieb zahlt der Schlüssel des Betreibers für alle. Damit er
sieht, wer wie viel verbraucht, schreibt jeder bezahlte Aufruf eine Zeile in
eine Monatsdatei (JSON Lines). Keine Inhalte - nur wer, wann, welcher Dienst,
wie viel. Die Kosten sind eine Schätzung aus den Listenpreisen in der
Konfiguration (`price_*`), keine Abrechnung: maßgeblich bleibt das Konto beim
Anbieter.

Am eigenen Rechner wird nichts erfasst - dort zahlt der Nutzer selbst.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def cost_tokens(endpoint, prompt: int, completion: int) -> float:
    """Geschätzte Kosten in US-Dollar für einen LLM-Aufruf."""
    return (prompt * (endpoint.price_in or 0.0)
            + completion * (endpoint.price_out or 0.0)) / 1_000_000


def cost_audio(engine, seconds: float) -> float:
    """Geschätzte Kosten in US-Dollar für transkribiertes Audio."""
    return seconds / 3600 * (engine.price_hour or 0.0)


def _datei(verzeichnis: Path, wann: datetime) -> Path:
    return verzeichnis / f"{wann:%Y-%m}.jsonl"


def record(verzeichnis: Path | None, user: str, kind: str, resource: str, *,
           seconds: float = 0.0, prompt_tokens: int = 0, completion_tokens: int = 0,
           cost: float = 0.0) -> None:
    """Eine Zeile anhängen. Scheitert das Schreiben, geht die Arbeit trotzdem
    weiter - das Protokoll ist Beobachtung, keine Schranke."""
    if verzeichnis is None:
        return
    jetzt = datetime.now(timezone.utc)
    zeile = {"t": jetzt.isoformat(timespec="seconds"), "user": user, "kind": kind,
             "resource": resource, "seconds": round(seconds, 1),
             "prompt_tokens": int(prompt_tokens), "completion_tokens": int(completion_tokens),
             "cost": round(cost, 6)}
    try:
        verzeichnis.mkdir(parents=True, exist_ok=True)
        with _datei(verzeichnis, jetzt).open("a", encoding="utf-8") as f:
            f.write(json.dumps(zeile, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Verbrauch nicht protokolliert: %s", exc)


def month_cost(verzeichnis: Path | None, user: str) -> float:
    """Geschätzte Kosten dieses Benutzers im laufenden Monat, in US-Dollar."""
    if verzeichnis is None:
        return 0.0
    pfad = _datei(verzeichnis, datetime.now(timezone.utc))
    summe = 0.0
    try:
        with pfad.open(encoding="utf-8") as f:
            for roh in f:
                try:
                    z = json.loads(roh)
                except ValueError:
                    continue
                if z.get("user") == user:
                    summe += float(z.get("cost") or 0)
    except OSError:
        return 0.0
    return summe


def lines_for(verzeichnis: Path, user: str) -> list[dict]:
    """Alle Zeilen eines Benutzers, für die Auskunft (Art. 15 DSGVO)."""
    zeilen = []
    for monat in sorted(months(verzeichnis)):
        for roh in (verzeichnis / f"{monat}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                z = json.loads(roh)
            except ValueError:
                continue
            if z.get("user") == user:
                zeilen.append(z)
    return zeilen


def prune(verzeichnis: Path, keep_months: int = 13) -> list[str]:
    """Monatsdateien löschen, die älter als `keep_months` Monate sind."""
    jetzt = datetime.now(timezone.utc)
    grenze = jetzt.year * 12 + jetzt.month - 1 - keep_months
    weg = []
    for monat in months(verzeichnis):
        try:
            jahr, mon = (int(x) for x in monat.split("-"))
        except ValueError:
            continue
        if jahr * 12 + mon - 1 < grenze:
            (verzeichnis / f"{monat}.jsonl").unlink(missing_ok=True)
            weg.append(monat)
    if weg:
        logger.info("Verbrauch gelöscht (älter als %d Monate): %s", keep_months, ", ".join(weg))
    return weg


def months(verzeichnis: Path) -> list[str]:
    if not verzeichnis.is_dir():
        return []
    return sorted((p.stem for p in verzeichnis.glob("*.jsonl")), reverse=True)


def summary(verzeichnis: Path, month: str) -> dict:
    """Summen je Benutzer und je Dienst für einen Monat (JJJJ-MM)."""
    pfad = verzeichnis / f"{month}.jsonl"
    je_user: dict[str, dict] = defaultdict(lambda: {
        "cost": 0.0, "seconds": 0.0, "prompt_tokens": 0, "completion_tokens": 0,
        "calls": 0, "resources": defaultdict(lambda: {"cost": 0.0, "seconds": 0.0,
                                                      "prompt_tokens": 0,
                                                      "completion_tokens": 0, "calls": 0})})
    if pfad.is_file():
        for roh in pfad.read_text(encoding="utf-8").splitlines():
            try:
                z = json.loads(roh)
            except ValueError:
                continue
            u = je_user[str(z.get("user"))]
            r = u["resources"][f'{z.get("kind")}: {z.get("resource")}']
            for ziel in (u, r):
                ziel["cost"] += float(z.get("cost") or 0)
                ziel["seconds"] += float(z.get("seconds") or 0)
                ziel["prompt_tokens"] += int(z.get("prompt_tokens") or 0)
                ziel["completion_tokens"] += int(z.get("completion_tokens") or 0)
                ziel["calls"] += 1
    users = []
    for name, u in sorted(je_user.items(), key=lambda kv: -kv[1]["cost"]):
        users.append({**{k: v for k, v in u.items() if k != "resources"}, "user": name,
                      "resources": [{"name": k, **v} for k, v in
                                    sorted(u["resources"].items(), key=lambda kv: -kv[1]["cost"])]})
    return {"month": month, "users": users,
            "total": round(sum(u["cost"] for u in users), 4), "generated": int(time.time())}
