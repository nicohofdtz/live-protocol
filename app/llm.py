"""Chat-Backends mit Failover über eine priorisierte Endpunktliste.

Ein Rechner im eigenen Netz ist nicht immer eingeschaltet - der Fallback ist
deshalb kein Sonderfall, sondern Normalbetrieb.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import httpx

from app.config import Endpoint

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(300.0, connect=5.0)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# EHOSTUNREACH auf einen Rechner im LAN. Der Fehler kann vorübergehend
# auftreten, während curl, Gateway und Internet funktionieren, und ohne
# Zutun wieder verschwinden (etwa bei WLAN-Stromsparmodus der Gegenstelle).
# Deshalb hier keine Ursachenbehauptung, sondern Prüfschritte.
UNREACHABLE_HINT = (
    "Host im LAN nicht erreichbar (EHOSTUNREACH). Prüfen: läuft die "
    "Gegenstelle und ist Ollama dort gestartet? Antwortet "
    "`curl <url>/api/version` von diesem Rechner? Der Fehler trat schon "
    "vorübergehend auf und verschwand von selbst - bei sporadischem Auftreten "
    "greifen Retry und Endpunkt-Failover."
)


class LLMError(Exception):
    pass


class AllEndpointsFailed(LLMError):
    pass


class RateLimited(LLMError):
    """HTTP 429. Kein Ausfall, sondern eine Bitte um Geduld."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Anfragelimit erreicht, erneut in {retry_after:.0f}s")


NO_QUOTA_HINT = (
    "Für dieses Modell steht kein Kontingent zur Verfügung (Limit 0 Anfragen "
    "pro Minute), deshalb hilft Warten nicht. Beobachtet bei Mistral: die "
    "Limits-Seite zeigt den Anspruch des Plans, nicht den Rest des Monats, "
    "und Kontingente gelten je Workspace für alle seine Schlüssel gemeinsam. "
    "Im Admin-Bereich unter API > Usage den Monatsverbrauch dieses Modells "
    "prüfen und unter Workspaces, ob der Schlüssel im erwarteten Workspace "
    "liegt. Andere Modelle können unbetroffen sein."
)


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after")
    try:
        return min(float(raw), 30.0) if raw else 3.0
    except ValueError:
        return 3.0


_FENCE_RE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Entfernt einen umschliessenden Code-Zaun.

    Mehrere Modelle verpacken ihr Markdown in ```markdown ... ```. Ungefiltert
    stuenden die Zaunzeilen woertlich im Protokollfenster.
    """
    match = _FENCE_RE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def strip_think(text: str) -> str:
    """Denkmodelle (Qwen3) liefern <think>-Blöcke, die nicht ins Protokoll gehören."""
    return _THINK_RE.sub("", text).strip()


def extract_json(text: str) -> dict:
    """Erstes JSON-Objekt aus einer Antwort holen, notfalls per Klammersuche."""
    text = strip_think(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"Keine gültige JSON-Antwort: {text[:200]!r}")


@dataclass
class Completion:
    text: str
    endpoint: str
    prompt_tokens: int
    completion_tokens: int


class ChatClient:
    """Spricht eine Liste von Endpunkten der Reihe nach an."""

    def __init__(self, endpoints: list[Endpoint], on_usage=None):
        if not endpoints:
            raise ValueError("Mindestens ein LLM-Endpunkt nötig")
        self.endpoints = endpoints
        # Wird nach jeder Antwort mit (Endpunkt, Completion) gerufen - für das
        # Verbrauchsprotokoll je Benutzer (app/usage.py).
        self.on_usage = on_usage

    def _melde(self, endpoint: Endpoint, result: Completion) -> Completion:
        if self.on_usage is not None:
            try:
                self.on_usage(endpoint, result)
            except Exception:  # noqa: BLE001 - Protokoll darf nichts abbrechen
                logger.exception("Verbrauch nicht erfasst")
        return result

    async def _call(
        self, endpoint: Endpoint, system: str, user: str, json_mode: bool, temperature: float
    ) -> Completion:
        payload: dict = {
            "model": endpoint.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if endpoint.disable_thinking:
            # Denkmodelle (Qwen3) verbrennen sonst den Großteil des Budgets im
            # reasoning-Feld und liefern gelegentlich leeren content. Gemessen:
            # 36s -> 7s. chat_template_kwargs/enable_thinking wirkt hier NICHT,
            # reasoning_effort schon.
            payload["reasoning_effort"] = "none"
        # Je Endpunkt konfiguriert, etwa reasoning_effort oder bei OpenRouter
        # die Anbieterwahl ohne Speicherung.
        payload.update(endpoint.extra)

        headers = {"Content-Type": "application/json"}
        if endpoint.api_key:
            headers["Authorization"] = f"Bearer {endpoint.api_key}"

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                endpoint.url.rstrip("/") + "/v1/chat/completions",
                headers=headers,
                json=payload,
            )
        if response.status_code == 429:
            # Ein Limit von 0 pro Minute ist keine Ueberlastung, sondern eine
            # fehlende Berechtigung - Wiederholen waere sinnlose Wartezeit.
            if response.headers.get("x-ratelimit-limit-req-minute") == "0":
                raise LLMError(NO_QUOTA_HINT)
            raise RateLimited(_retry_after(response))
        if response.status_code >= 400:
            raise LLMError(f"HTTP {response.status_code}: {response.text[:300]}")

        data = response.json()
        try:
            content = _text_of(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unerwartete Antwortstruktur: {str(data)[:200]}") from exc
        usage = data.get("usage", {})
        return Completion(
            text=strip_think(content),
            endpoint=endpoint.name,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def complete(
        self, system: str, user: str, json_mode: bool = False, temperature: float = 0.2
    ) -> Completion:
        errors: list[str] = []
        for endpoint in self.endpoints:
            try:
                return self._melde(endpoint, await self._call(
                    endpoint, system, user, json_mode, temperature))
            except RateLimited as exc:
                # Warten statt aufgeben: Ein Limit je Minute (bei GLM 5.3
                # 100.000 Tokens) ist nach Sekunden wieder frei, und eine lange
                # Zusammenfassung kostet davon auf einmal fast die Hälfte.
                result = await self._retry_limited(endpoint, exc, system, user,
                                                   json_mode, temperature, errors)
                if result is not None:
                    return self._melde(endpoint, result)
            except httpx.ConnectError as exc:
                hint = UNREACHABLE_HINT if _is_host_unreachable(exc) else str(exc)
                errors.append(f"{endpoint.name}: {hint}")
                logger.warning("Endpunkt %s nicht erreichbar, versuche nächsten", endpoint.name)
            except (httpx.TimeoutException, LLMError) as exc:
                errors.append(f"{endpoint.name}: {exc}")
                logger.warning("Endpunkt %s fehlgeschlagen: %s", endpoint.name, exc)
        raise AllEndpointsFailed("Kein Endpunkt lieferte eine Antwort.\n" + "\n".join(errors))

    async def _retry_limited(self, endpoint, exc, system, user, json_mode,
                             temperature, errors) -> Completion | None:
        pause = exc.retry_after
        for versuch in range(len(_LIMIT_PAUSES)):
            logger.warning("Endpunkt %s ist im Limit, warte %.0f s", endpoint.name, pause)
            await asyncio.sleep(pause)
            try:
                return await self._call(endpoint, system, user, json_mode, temperature)
            except RateLimited as again:
                pause = max(again.retry_after, _LIMIT_PAUSES[versuch])
            except (LLMError, httpx.HTTPError) as retry_exc:
                errors.append(f"{endpoint.name}: {retry_exc}")
                return None
        errors.append(f"{endpoint.name}: Anfragelimit auch nach mehreren Versuchen erreicht")
        return None

    async def health(self) -> list[dict]:
        """Erreichbarkeit aller Endpunkte prüfen (für Start und Statusanzeige)."""
        results = []
        for endpoint in self.endpoints:
            entry = {"name": endpoint.name, "model": endpoint.model, "url": endpoint.url}
            try:
                # Ohne Schluessel meldet ein Cloud-Endpunkt 401 und sieht damit
                # kaputt aus, obwohl er einwandfrei erreichbar ist.
                headers = ({"Authorization": f"Bearer {endpoint.api_key}"}
                           if endpoint.api_key else {})
                async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=4.0)) as client:
                    response = await client.get(
                        endpoint.url.rstrip("/") + "/v1/models", headers=headers)
                entry["ok"] = response.status_code < 400
                if not entry["ok"]:
                    entry["error"] = f"HTTP {response.status_code}"
            except httpx.ConnectError as exc:
                entry["ok"] = False
                entry["error"] = (UNREACHABLE_HINT if _is_host_unreachable(exc)
                                  else _describe(exc))
            except Exception as exc:  # noqa: BLE001
                entry["ok"] = False
                entry["error"] = _describe(exc)
            results.append(entry)
        return results


# Wartezeiten nach wiederholtem 429. Zusammen gut eine Minute - so lange, wie
# ein Limit je Minute höchstens braucht, um wieder frei zu werden.
_LIMIT_PAUSES = (15.0, 30.0, 30.0)


def _text_of(content) -> str:
    """Den Antworttext herausholen, auch wenn er in Blöcken kommt.

    Denkmodelle bei Mistral (GLM 5.3) liefern content als Liste aus einem
    thinking- und einem text-Block. Nur der Text ist die Antwort."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content
                       if isinstance(block, dict) and block.get("type") == "text")
    raise TypeError(f"content ist {type(content).__name__}")


def _describe(exc: BaseException) -> str:
    """Manche httpx-Fehler haben eine leere Meldung - dann sagt wenigstens der
    Typ, was los war, statt einer leeren Zeile in der Oberfläche."""
    text = str(exc).strip()
    return text or type(exc).__name__


def _is_host_unreachable(exc: BaseException) -> bool:
    """EHOSTUNREACH erkennen - httpx verpackt den OSError, daher die Kette prüfen."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if getattr(current, "errno", None) == 65:
            return True
        if "No route to host" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False
