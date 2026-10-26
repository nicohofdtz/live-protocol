"""Minimaler OpenAI-kompatibler Chat-Client (stdlib, keine Abhängigkeiten).

Reicht für das Experiment; die App bekommt später den httpx-Adapter aus
der App. Ollama spricht unter /v1 den OpenAI-Dialekt.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

# Qwen3-Modelle geben Denkschritte in <think>-Blöcken aus, die nicht ins
# Protokoll gehören und JSON-Antworten unparsbar machen.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    pass


# macOS 26 "Local Network Privacy": Prozesse brauchen eine eigene Freigabe, um
# LAN-*Peers* zu erreichen (Router und Internet sind ausgenommen). Der
# Homebrew-Python hat sie nicht, /usr/bin/curl schon. Solange die Freigabe
# fehlt, läuft der Verkehr über curl statt über urllib.
_USE_CURL = False


def _post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    """POST mit JSON-Antwort. Fällt bei EHOSTUNREACH einmalig auf curl zurück."""
    global _USE_CURL
    body = json.dumps(payload).encode("utf-8")

    if not _USE_CURL:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMError(
                f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:500]}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if getattr(reason, "errno", None) != 65 and "No route to host" not in str(reason):
                raise LLMError(f"Verbindung fehlgeschlagen: {reason}") from exc
            _USE_CURL = True

    curl = shutil.which("curl")
    if curl is None:
        raise LLMError("Weder urllib noch curl können den Host erreichen")
    cmd = [curl, "-s", "--fail-with-body", "--max-time", str(int(timeout)), url]
    for key, value in headers.items():
        cmd += ["-H", f"{key}: {value}"]
    cmd += ["--data-binary", "@-"]
    proc = subprocess.run(cmd, input=body, capture_output=True)
    if proc.returncode != 0:
        raise LLMError(
            f"curl rc={proc.returncode}: {proc.stdout.decode('utf-8', 'replace')[:500]}"
        )
    return json.loads(proc.stdout.decode("utf-8"))


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def extract_json(text: str) -> dict:
    """Holt das erste JSON-Objekt aus einer Antwort, notfalls per Klammersuche."""
    text = strip_think(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fenced code block?
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    # Erste balancierte {...}-Sequenz
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
    raise LLMError(f"Keine gültige JSON-Antwort: {text[:300]!r}")


def chat(
    system: str,
    user: str,
    model: str,
    base_url: str,
    api_key: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    timeout: float = 600.0,
    retries: int = 3,
    disable_thinking: bool = True,
) -> tuple[str, dict, float]:
    """Ein Chat-Completion-Aufruf. Liefert (Text, usage, Latenz in Sekunden).

    Verbindungsfehler werden mit Backoff wiederholt: ein Modellserver im
    eigenen Netz ist gelegentlich kurz nicht erreichbar.
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if disable_thinking:
        payload["reasoning_effort"] = "none"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started = time.monotonic()
    last_error: Exception | None = None
    data = None
    for attempt in range(retries):
        try:
            data = _post_json(url, payload, headers, timeout)
            break
        except LLMError as exc:
            last_error = exc
            if "HTTP 4" in str(exc):
                raise
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    if data is None:
        raise LLMError(f"Fehlgeschlagen nach {retries} Versuchen: {last_error}")
    latency = time.monotonic() - started

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Unerwartete Antwortstruktur: {str(data)[:300]}") from exc

    return content, data.get("usage", {}), latency
