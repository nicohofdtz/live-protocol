"""Lokale Einstellungen der ausgelieferten Anwendung.

Im Desktop-Betrieb bringt der Nutzer eigene Zugangsdaten mit. Die gehören
weder in eine `.env` neben die App (dort ist oft kein Schreibrecht)
noch in den Browser-Speicher, sondern in eine Datei im Benutzerprofil, die nur
er selbst lesen kann.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from app.paths import config_file

logger = logging.getLogger(__name__)

# Felder, deren Inhalt die Oberfläche nie zurückbekommt.
SECRETS = {"mistral_api_key", "openrouter_api_key", "api_key"}

# Der OpenAI-kompatible Anbieter, wie er unter "openai_compat" liegt.
PROVIDER_FIELDS = ("preset", "base_url", "api_key", "live_model", "summary_model",
                   "transcription_model")


def load() -> dict[str, Any]:
    path = config_file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Lokale Konfiguration nicht lesbar (%s), ignoriere sie", exc)
        return {}


def save(data: dict[str, Any]) -> None:
    path = config_file()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass   # Windows kennt den Modus nicht
    tmp.replace(path)


def update(patch: dict[str, Any]) -> dict[str, Any]:
    """Führt Änderungen zusammen. Ein leerer Wert löscht das Feld."""
    data = load()
    for key, value in patch.items():
        if value in (None, ""):
            data.pop(key, None)
        else:
            data[key] = value
    save(data)
    return data


def redacted(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fassung für die Oberfläche: Geheimnisse werden zu einem Ja/Nein."""
    data = load() if data is None else data
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in SECRETS:
            out[f"{key}_set"] = bool(value)
        elif isinstance(value, dict):
            out[key] = redacted(value)
        else:
            out[key] = value
    return out


def settings_update(data: dict[str, Any]) -> dict[str, Any]:
    """Was die lokalen Einstellungen an der Konfiguration ändern.

    Ein eingetragener Anbieter ersetzt den aus der .env ganz, nicht Feld für
    Feld - sonst entstünde eine Mischung aus zwei Anbietern. Ältere Dateien
    kennen statt des Anbieters einen OpenRouter-Schlüssel und einen eigenen
    Endpunkt fürs Live-Protokoll; beide gelten weiter, bis der Anbieter in
    den Einstellungen gespeichert wird."""
    update: dict[str, Any] = {}
    if data.get("mistral_api_key"):
        update["mistral_api_key"] = data["mistral_api_key"]
    anbieter = data.get("openai_compat")
    if isinstance(anbieter, dict) and anbieter:
        for feld in PROVIDER_FIELDS:
            update[f"openai_compat_{feld}"] = anbieter.get(feld) or ("" if feld != "api_key"
                                                                     else None)
        update["openrouter_api_key"] = None
        return update
    if data.get("openrouter_api_key"):
        update["openrouter_api_key"] = data["openrouter_api_key"]
        return update
    alt = data.get("custom_llm") or {}
    if isinstance(alt, dict) and alt.get("url") and alt.get("model"):
        update.update(openai_compat_preset="custom", openai_compat_base_url=alt["url"],
                      openai_compat_live_model=alt["model"],
                      openai_compat_summary_model=alt["model"],
                      openai_compat_transcription_model="",
                      openai_compat_api_key=alt.get("api_key") or None)
    return update
