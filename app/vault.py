"""Gespeicherte Sitzungen je Benutzer - als Chiffrat.

Anwendungsfall: am Telefon aufnehmen, am Rechner nachbearbeiten. Dafür muss
der Stand irgendwo zwischen den Geräten liegen. Damit das nicht dem Grundsatz
widerspricht, dass der Server keine fremden Besprechungen hält,
verschlüsselt der Browser das Projekt, bevor es hochgeht:

- Aus dem Tresor-Passwort des Nutzers leitet der Browser per PBKDF2 einen
  Schlüssel ab. Der wickelt einen zufälligen Hauptschlüssel ein (AES-GCM),
  und nur dieser eingewickelte Schlüssel liegt hier. Ein neues Passwort heißt
  neu einwickeln, nicht alles neu verschlüsseln.
- Jede Sitzung ist das Projekt-ZIP, im Browser mit dem Hauptschlüssel
  verschlüsselt. Auch Titel und Datum für die Liste sind verschlüsselt.

Der Server - und damit auch der Betreiber - sieht also nur Größen, Zeitpunkte
und Zufallsbytes. Die Kehrseite: Wer sein Passwort vergisst, kommt an nichts
mehr heran; es bleibt nur, den Tresor zu leeren.

Nur im gehosteten Betrieb. Am eigenen Rechner gibt es die Projektdatei.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

ID = re.compile(r"^[0-9a-f]{16,32}$")
# Base64 ohne Zeilenumbrüche, grob begrenzt - mehr braucht keines der Felder.
_B64 = re.compile(r"^[A-Za-z0-9+/=_-]{0,8192}$")


class VaultError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _ordner(basis: Path, user: str) -> Path:
    # Gehasht statt als Name: kein Pfad aus der Anfrage, und die Verzeichnis-
    # liste verrät nicht, wer einen Tresor hat.
    return basis / hashlib.sha256(user.encode("utf-8")).hexdigest()[:32]


def _lies(pfad: Path, vorgabe):
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return vorgabe


def _schreib(pfad: Path, daten) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten), encoding="utf-8")
    os.replace(tmp, pfad)


def status(basis: Path, user: str, quota_mb: int) -> dict:
    ordner = _ordner(basis, user)
    schluessel = _lies(ordner / "key.json", None)
    index = _lies(ordner / "index.json", {})
    items = [{"id": i, **{k: v for k, v in e.items() if k in ("size", "updated", "meta")}}
             for i, e in sorted(index.items(), key=lambda kv: -kv[1].get("updated", 0))]
    return {"key": schluessel, "items": items, "quota": quota_mb * 1024 * 1024,
            "used": sum(e.get("size", 0) for e in index.values())}


def set_key(basis: Path, user: str, payload: dict) -> dict:
    felder = {k: payload.get(k) for k in ("salt", "wrapped")}
    if not all(isinstance(v, str) and v and _B64.match(v) for v in felder.values()):
        raise VaultError(400, "Schlüsseldaten unvollständig")
    iterationen = payload.get("iterations")
    if not isinstance(iterationen, int) or not 100_000 <= iterationen <= 10_000_000:
        raise VaultError(400, "Ungültige Iterationszahl")
    pfad = _ordner(basis, user) / "key.json"
    if pfad.exists() and payload.get("replace") is not True:
        raise VaultError(409, "Es gibt schon einen Tresor")
    daten = {**felder, "iterations": iterationen, "updated": int(time.time())}
    _schreib(pfad, daten)
    return daten


def put_item(basis: Path, user: str, item_id: str, daten: bytes, meta: str,
             quota_mb: int) -> dict:
    if not ID.match(item_id):
        raise VaultError(400, "Ungültige Kennung")
    if not _B64.match(meta or ""):
        raise VaultError(400, "Ungültige Metadaten")
    ordner = _ordner(basis, user)
    if not (ordner / "key.json").exists():
        raise VaultError(409, "Zuerst ein Tresor-Passwort festlegen")
    index = _lies(ordner / "index.json", {})
    belegt = sum(e.get("size", 0) for i, e in index.items() if i != item_id)
    if belegt + len(daten) > quota_mb * 1024 * 1024:
        raise VaultError(413, f"Der Speicher ({quota_mb} MB) ist voll. Lösche alte Sitzungen.")
    tmp = ordner / f"{item_id}.tmp"
    tmp.write_bytes(daten)
    os.replace(tmp, ordner / f"{item_id}.bin")
    index[item_id] = {"size": len(daten), "updated": int(time.time()), "meta": meta}
    _schreib(ordner / "index.json", index)
    return {"id": item_id, **index[item_id]}


def item_path(basis: Path, user: str, item_id: str) -> Path:
    if not ID.match(item_id):
        raise VaultError(400, "Ungültige Kennung")
    pfad = _ordner(basis, user) / f"{item_id}.bin"
    if not pfad.is_file():
        raise VaultError(404, "Nicht gefunden")
    return pfad


def delete_item(basis: Path, user: str, item_id: str) -> None:
    pfad = item_path(basis, user, item_id)
    ordner = pfad.parent
    index = _lies(ordner / "index.json", {})
    index.pop(item_id, None)
    _schreib(ordner / "index.json", index)
    pfad.unlink(missing_ok=True)


def reset(basis: Path, user: str) -> None:
    """Alles weg, auch der Schlüssel - der einzige Weg nach einem
    vergessenen Passwort."""
    shutil.rmtree(_ordner(basis, user), ignore_errors=True)
