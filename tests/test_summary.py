"""Die Zusammenfassung nach dem Termin: Prompt, Endpunkt, Schalter."""

from __future__ import annotations

import pytest

from app import summary
from app.output_types import load_output_types
from tests.test_api import hosted, per_proxy  # noqa: F401 - Fixture
from tests.test_session import StubChat, _meeting, settings, silence, tone  # noqa: F401

MEETING = load_output_types()["meeting"]
TRANSKRIPT = [
    {"text": "Wir fangen mit dem Budget an.", "seconds": 0},
    {"text": "[Aufnahme unterbrochen - etwa 40 s fehlen]", "seconds": 60, "gap": True},
    {"text": "Also machen wir das so.", "seconds": 125},
]


def test_prompt_enthaelt_regeln_rahmen_hinweise_und_luecke():
    system, user = summary.build_prompt(
        MEETING, TRANSKRIPT, context={"topic": "Haushalt 2027"},
        detail="knapp", freitext="Bitte auf Englisch.", feedback=["Namen ausschreiben"])
    assert "Keine Zusammenhänge herstellen" in system
    assert summary.DETAIL["knapp"] in system
    assert "Haushalt 2027" in system and "keine Vorgabe" in system
    assert "Namen ausschreiben" in system and "Bitte auf Englisch." in system
    assert "[2:05] Also machen wir das so." in user
    assert "[1:00] [Aufnahme unterbrochen" in user, "die Lücke muss sichtbar bleiben"


def test_bausteine_folgen_der_auswahl():
    alle = {b.id: b.instruction for b in MEETING.summary_blocks}
    system, _ = summary.build_prompt(MEETING, TRANSKRIPT)  # Vorgabe: alle an
    assert all(anweisung in system for anweisung in alle.values())
    system, _ = summary.build_prompt(MEETING, TRANSKRIPT, blocks=["aufgaben"])
    assert alle["aufgaben"] in system and alle["kurzfassung"] not in system
    system, _ = summary.build_prompt(MEETING, TRANSKRIPT, blocks=[])
    assert "Außerdem:" not in system


def test_unbekannte_detailstufe_faellt_auf_standard():
    system, _ = summary.build_prompt(MEETING, TRANSKRIPT, detail="7 von 10")
    assert summary.DETAIL["standard"] in system


async def test_ergebnis_ohne_codezaun():
    class Umzaeunt(StubChat):
        async def complete(self, system, user, json_mode=False, temperature=0.2):
            antwort = await super().complete(system, user, json_mode, temperature)
            antwort.text = "```markdown\n# Protokoll\n```"
            return antwort

    ergebnis = await summary.summarize(Umzaeunt(), MEETING, TRANSKRIPT)
    assert ergebnis.markdown == "# Protokoll"


# -- Endpunkt ------------------------------------------------------------------

def test_endpunkt_fasst_zusammen_ohne_sitzung(hosted, monkeypatch):  # noqa: F811
    from app.main import settings as app_settings

    gesehen = {}

    async def falsch_summarize(chat, output_type, entries, **optionen):
        gesehen.update(optionen, modell=chat.endpoints[0].model, n=len(entries))
        return summary.SummaryResult("# Ergebnis", "stub", 10, 5, 1.2)

    monkeypatch.setattr(summary, "summarize", falsch_summarize)
    antwort = hosted.post("/api/summarize", json={
        "model": "eu", "output_type": "meeting", "transcript": TRANSKRIPT,
        "context": {"topic": "Haushalt"}, "feedback": ["kürzer"],
        "options": {"detail": "ausfuehrlich", "blocks": ["aufgaben"], "freitext": "x"},
    })
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["markdown"] == "# Ergebnis"
    assert gesehen["modell"] == "zai-glm-5-3"
    assert gesehen["detail"] == "ausfuehrlich" and gesehen["blocks"] == ["aufgaben"]
    assert gesehen["feedback"] == ["kürzer"] and gesehen["n"] == 3


def test_zwei_modelle_in_der_eu_gruendlich_zuerst(hosted):  # noqa: F811
    config = hosted.get("/api/config").json()
    assert [m["id"] for m in config["summary_models"]] == ["eu", "eu-schnell"]
    assert all(m["privacy"] and m["cost"] and "EU" in m["label"]
               for m in config["summary_models"])


def test_nur_eingerichtete_modelle_stehen_zur_wahl(hosted, monkeypatch):  # noqa: F811
    """Ein Eintrag, der beim Klick scheitert, gehört nicht in die Auswahl."""
    from app.main import settings as app_settings

    monkeypatch.setattr(app_settings, "mistral_api_key", None)
    config = hosted.get("/api/config").json()
    assert config["summary_models"] == []
    antwort = hosted.post("/api/summarize", json={
        "model": "eu", "transcript": TRANSKRIPT})
    assert antwort.status_code == 400


def test_zu_langes_transkript_wird_abgewiesen(hosted):  # noqa: F811
    riesig = [{"text": "x" * 500_000, "seconds": 0}]
    antwort = hosted.post("/api/summarize",
                          json={"model": "eu", "transcript": riesig})
    assert antwort.status_code == 413


def test_hinter_dem_proxy_ohne_anmeldung_keine_zusammenfassung(per_proxy):  # noqa: F811
    assert per_proxy.post("/api/summarize", json={"model": "eu"}).status_code == 401


# -- Schalter Live-Protokoll ------------------------------------------------------

async def test_ohne_live_protokoll_kein_einziger_llm_aufruf(settings):  # noqa: F811
    """Der Schalter spart Geld nur, wenn er auf dem Server wirkt."""
    chat = StubChat()
    session = _meeting(settings, strategy="delta", chat=chat)
    session.live_protocol = False
    session.start()
    await session.add_audio(tone(18.0) + silence(2.0) + tone(18.0))
    await session.add_feedback("Bitte Namen ausschreiben.")
    await session.stop()

    assert session.transcript, "das Transkript läuft trotzdem"
    assert chat.calls == 0
    assert session.feedback == ["Bitte Namen ausschreiben."]


def test_sitzung_ohne_angabe_hat_kein_live_protokoll(hosted):  # noqa: F811
    from app.main import sessions

    antwort = hosted.post("/api/sessions",
                          json={"engine": "voxtral"}).json()
    assert antwort["live_protocol"] is False
    assert sessions[antwort["id"]].live_protocol is False
    an = hosted.post("/api/sessions",
                     json={"engine": "voxtral", "live_protocol": True}).json()
    assert an["live_protocol"] is True


BENANNT = [
    {"text": "Ich übernehme die Hüpfburg.", "seconds": 0, "speaker": "Martina"},
    {"text": "Gut.", "seconds": 5, "speaker": "Sprecher 3"},
    {"text": "[Aufnahme unterbrochen - etwa 20 s fehlen]", "seconds": 8, "gap": True},
]


def test_mit_personen_stehen_namen_vor_den_zeilen():
    system, user = summary.build_prompt(MEETING, BENANNT, personen=True)
    assert summary.PERSONEN in system
    assert "[0:00] Martina: Ich übernehme die Hüpfburg." in user
    assert "[0:05] Sprecher 3: Gut." in user
    assert "[0:08] [Aufnahme unterbrochen" in user, "eine Lücke hat keinen Sprecher"


def test_ohne_personen_gehen_keine_namen_mit():
    """Namen sind Personendaten: Ohne die Option erscheinen sie nicht im
    Prompt, selbst wenn sie mitgeschickt wurden."""
    system, user = summary.build_prompt(MEETING, BENANNT)
    assert summary.PERSONEN not in system
    assert "Martina" not in user


def test_personen_ohne_sprecher_im_transkript_bleibt_aus():
    system, _ = summary.build_prompt(MEETING, TRANSKRIPT, personen=True)
    assert summary.PERSONEN not in system


# -- Anmerkungen --------------------------------------------------------

async def test_anmerkungen_kommen_als_ersetzungen():
    class Antwortet(StubChat):
        async def complete(self, system, user, json_mode=False, temperature=0.2):
            antwort = await super().complete(system, user, json_mode, temperature)
            self.gesehen = (system, user, json_mode)
            antwort.text = ('{"ersetzungen": [{"alt": "Meier", "neu": "Maier", "alle": true},'
                            ' {"alt": ""}, {"alt": "Maier", "neu": "Maier"}]}')
            return antwort

    chat = Antwortet()
    ergebnis = await summary.revise(chat, "# Protokoll\n\nFrau Meier übernimmt.",
                                    [{"quote": "Meier", "comment": "gemeint ist Frau Maier"},
                                     {"comment": "Aufgaben fett"}])
    assert ergebnis["replacements"] == [{"alt": "Meier", "neu": "Maier", "alle": True}]
    system, user, json_mode = chat.gesehen
    assert json_mode and "zeichengenau" in system
    assert "1. Zur Stelle „Meier“: gemeint ist Frau Maier" in user
    assert "2. Allgemein: Aufgaben fett" in user


def test_revise_ohne_anmerkung_wird_abgewiesen(hosted):  # noqa: F811
    assert hosted.post("/api/revise",
                       json={"markdown": "# x", "annotations": []}).status_code == 400
