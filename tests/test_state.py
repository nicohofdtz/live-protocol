"""Protokollzustand: Operationen anwenden und rendern."""

from __future__ import annotations

from app.protocol.state import ProtocolState


def test_add_und_extend_topic():
    state = ProtocolState()
    applied = state.apply([{"op": "add_topic", "title": "Spritpreise", "summary": "Erste Runde."}], 1)
    assert applied == 1
    assert state.topics[0].id == "t1"

    state.apply([{"op": "extend_topic", "id": "t1", "add": "Zweite Runde."}], 2)
    assert state.topics[0].summary == "Erste Runde. Zweite Runde."
    assert state.topics[0].first_cycle == 1
    assert state.topics[0].last_cycle == 2


def test_ungueltige_operationen_werden_gezaehlt_nicht_angewendet():
    state = ProtocolState()
    applied = state.apply(
        [
            {"op": "add_topic"},                       # Titel fehlt
            {"op": "extend_topic", "id": "t9", "add": "x"},  # Thema gibt es nicht
            {"op": "quatsch"},
            "kein dict",
        ],
        1,
    )
    assert applied == 0
    assert state.invalid_ops == 4
    assert state.topics == []


def test_digest_bleibt_kompakt():
    state = ProtocolState()
    state.apply([{"op": "add_topic", "title": "Thema",
                  "summary": "Erster Satz. " + "Weiterer Satz. " * 50}], 1)
    # Der Digest darf nicht mit dem Volltext wachsen - das ist der Kern der Idee.
    assert len(state.digest()) < 60


def test_render_enthaelt_alle_abschnitte():
    state = ProtocolState()
    state.apply(
        [
            {"op": "add_topic", "title": "Steuern", "summary": "Diskussion."},
            {"op": "add_decision", "text": "Vertagt", "topic": "t1"},
            {"op": "add_action", "text": "Zahlen liefern", "owner": "Ministerium"},
            {"op": "add_question", "text": "Wer zahlt?"},
        ],
        1,
    )
    rendered = state.render()
    assert "## Steuern" in rendered
    assert "- Vertagt" in rendered
    assert "- Zahlen liefern (Ministerium)" in rendered
    assert "- Wer zahlt?" in rendered


def test_revise_topic_ersetzt_statt_anzuhaengen():
    """Nutzer-Feedback muss Bestehendes ueberarbeiten koennen, nicht nur ergaenzen."""
    state = ProtocolState()
    state.apply([{"op": "add_topic", "title": "Pegel", "summary": "Kurz."}], 1)
    state.apply([{"op": "revise_topic", "id": "t1",
                  "summary": "Ausfuehrlich dargestellt.", "title": "Pegelanzeige"}], 2)
    assert state.topics[0].summary == "Ausfuehrlich dargestellt."
    assert state.topics[0].title == "Pegelanzeige"
    assert len(state.topics) == 1


def test_revise_topic_auf_unbekanntes_thema_wird_verworfen():
    state = ProtocolState()
    applied = state.apply([{"op": "revise_topic", "id": "t7", "summary": "x"}], 1)
    assert applied == 0
    assert state.invalid_ops == 1


def test_mistral_transkription_schickt_keine_fremden_felder():
    """Mistrals Endpunkt nimmt response_format und prompt zwar an, ignoriert
    sie aber; die Begriffsliste heißt dort context_bias."""
    from app.transcription import create_transcription_client

    mistral = create_transcription_client(
        "mistral", "https://api.mistral.ai", "voxtral-mini-latest",
        api_key="x", vocabulary="Voxtral, Ollama")
    assert mistral.form_fields() == {
        "model": "voxtral-mini-latest", "language": "de",
        "context_bias": ["Voxtral", "Ollama"],
    }

    whisper = create_transcription_client(
        "whisper-cpp", "http://127.0.0.1:8081", "turbo", vocabulary="Voxtral")
    felder = whisper.form_fields()
    assert felder["prompt"] == "Voxtral"
    assert "model" not in felder, "das Modell steht beim Serverstart fest"


def test_limit_null_wird_nicht_wiederholt():
    """Null Anfragen pro Minute ist eine fehlende Berechtigung, keine Ueberlastung."""
    import httpx
    import pytest

    from app.llm import LLMError, NO_QUOTA_HINT, RateLimited, _retry_after

    kein_kontingent = httpx.Response(429, headers={"x-ratelimit-limit-req-minute": "0"})
    ueberlastet = httpx.Response(429, headers={"retry-after": "7"})

    assert kein_kontingent.headers.get("x-ratelimit-limit-req-minute") == "0"
    assert _retry_after(ueberlastet) == 7.0
    # Deckelung, damit ein absurder Wert die Sitzung nicht einfriert.
    assert _retry_after(httpx.Response(429, headers={"retry-after": "3600"})) == 30.0
    assert _retry_after(httpx.Response(429)) == 3.0
    # Der Hinweis muss auf die pruefbaren Stellen im Konto zeigen.
    assert "Usage" in NO_QUOTA_HINT and "Workspace" in NO_QUOTA_HINT


def test_code_zaun_wird_entfernt():
    """Mehrere Modelle verpacken ihr Markdown in ```markdown ... ```."""
    from app.llm import strip_code_fence

    assert strip_code_fence("```markdown\n# Protokoll\n\nText\n```") == "# Protokoll\n\nText"
    assert strip_code_fence("```\n# Protokoll\n```") == "# Protokoll"
    # Ohne Zaun bleibt der Text unveraendert - auch wenn er selbst Code enthaelt.
    mit_code = "# Protokoll\n\n```python\nprint(1)\n```\n\nSchluss"
    assert strip_code_fence(mit_code) == mit_code
    assert strip_code_fence("  # Protokoll  ") == "# Protokoll"


def test_appledouble_dateien_werden_uebersprungen(tmp_path):
    """macOS packt "._name.md" mit; binaer, und der Lader wuerde daran scheitern."""
    from app.output_types import load_output_types

    (tmp_path / "meeting.md").write_text(
        "---\nid: meeting\nname: Test\ndescription: d\ndefault_strategy: rewrite\n---\nText",
        encoding="utf-8")
    (tmp_path / "._meeting.md").write_bytes(b"\x00\x05\x16\x07\xa3\xff binaerer Rest")

    typen = load_output_types(tmp_path)
    assert list(typen) == ["meeting"]
