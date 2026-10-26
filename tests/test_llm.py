"""Der LLM-Client: Antwortformen, Zusatzfelder je Endpunkt, Anfragelimit."""

from __future__ import annotations

import json

import httpx
import pytest

from app import llm
from app.config import Endpoint


def _client_mit(monkeypatch, antworten: list[httpx.Response]):
    """ChatClient, dessen Anfragen an eine Attrappe gehen. Liefert die
    gesendeten Nutzdaten zum Nachsehen."""
    gesendet: list[dict] = []
    echt = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        gesendet.append(json.loads(request.content))
        return antworten.pop(0)

    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: echt(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm, "_LIMIT_PAUSES", (0.0, 0.0, 0.0))
    return gesendet


def _ok(content) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": {"prompt_tokens": 5, "completion_tokens": 2}})


ENDPUNKT = Endpoint(name="glm", type="mistral", url="https://api.eu.mistral.ai",
                    model="zai-glm-5-3", api_key="k", local=False,
                    extra={"reasoning_effort": "high"})


async def test_denkblock_wird_verworfen_nur_der_text_zaehlt(monkeypatch):
    """GLM 5.3 bei Mistral liefert content als Liste aus thinking und text."""
    _client_mit(monkeypatch, [_ok([
        {"type": "thinking", "thinking": [{"type": "text", "text": "Überlegung"}]},
        {"type": "text", "text": "## Protokoll"},
    ])])
    antwort = await llm.ChatClient([ENDPUNKT]).complete("s", "u")
    assert antwort.text == "## Protokoll"


async def test_zusatzfelder_des_endpunkts_gehen_mit(monkeypatch):
    gesendet = _client_mit(monkeypatch, [_ok("ok")])
    await llm.ChatClient([ENDPUNKT]).complete("s", "u")
    assert gesendet[0]["reasoning_effort"] == "high"
    assert gesendet[0]["model"] == "zai-glm-5-3"


async def test_anfragelimit_wird_mehrfach_abgewartet(monkeypatch):
    """Ein Limit je Minute ist nach Sekunden frei - einmal Warten reicht bei
    einer langen Zusammenfassung nicht immer."""
    limit = httpx.Response(429, headers={"retry-after": "0"})
    gesendet = _client_mit(monkeypatch, [limit, limit, limit, _ok("endlich")])
    monkeypatch.setattr(llm, "_retry_after", lambda r: 0.0)
    antwort = await llm.ChatClient([ENDPUNKT]).complete("s", "u")
    assert antwort.text == "endlich"
    assert len(gesendet) == 4


async def test_dauerhaftes_limit_gibt_auf(monkeypatch):
    limit = httpx.Response(429, headers={"retry-after": "0"})
    _client_mit(monkeypatch, [limit] * 5)
    monkeypatch.setattr(llm, "_retry_after", lambda r: 0.0)
    with pytest.raises(llm.AllEndpointsFailed, match="Anfragelimit"):
        await llm.ChatClient([ENDPUNKT]).complete("s", "u")
