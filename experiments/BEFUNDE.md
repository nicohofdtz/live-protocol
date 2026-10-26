# Befunde: Vergleich der Fortschreibungsstrategien

Testmaterial: 15:41 Diskussionsrunde (deutsch, mehrere Sprecher, teilweise
durcheinander), transkribiert mit whisper.cpp large-v3-turbo q5_0.
18.708 Zeichen Transkript, 11 Zyklen à 90 s.
Protokollmodell: `qwen3.5:9b` lokal via Ollama.

Drei Arme:

- **A** naiver Volldurchlauf — Protokoll + neues Segment, „aktualisiere"
- **B** Volldurchlauf mit Veraltet-Hinweis
- **C** Delta/Append in eine Struktur

---

## 1. Der Anchoring-Effekt ist nicht aufgetreten

Die Annahme, auf der der Delta-Vorschlag beruhte, hat sich über 16 Minuten
**nicht bestätigt**. Kein Arm zeigt einen Abfall der Abdeckung zu späteren
Zyklen hin — auch der naive Arm A nicht, der ihn am deutlichsten zeigen müsste.
Der letzte Zyklus ist bei allen drei Armen voll abgedeckt.

Einschränkung: 16 Minuten sind nicht 60. Der beschriebene Effekt kann später
einsetzen; dieser Test kann ihn weder belegen noch ausschließen.

Die Abdeckungsmetrik selbst trägt ohnehin nur grob (Nenner von 4–7 Begriffen je
Zyklus). Sie taugt, um einen **Zusammenbruch** zu erkennen, nicht für feine
Qualitätsunterschiede. Ein erster Anlauf mit einer naiveren Metrik war komplett
wertlos — sie hatte „naja", „glaube" und „will" als kennzeichnende Begriffe
ausgewählt. Erst die Beschränkung auf großgeschriebene Wörter (deutsche
Substantive) machte sie brauchbar.

## 2. Qualität: der Veraltet-Hinweis gewinnt

| Arm | Zeichen | Überschriften | Beurteilung |
|---|---|---|---|
| A naiv | 6786 | 13 | **Fehlerhaft.** Das Endprotokoll trägt den Titel „(Fortsetzung)" und beginnt bei „## 4." — der Arm hat seine eigenen früheren Abschnitte stillschweigend verloren. |
| B Veraltet-Hinweis | 7679 | 5 | **Beste Fassung.** Geschlossene Gliederung 1–5, ausformuliert, ordnet jedem Punkt seine Rolle in der Debatte zu. |
| C Delta | 5387 | 4 | Sachlich richtig, aber **unterteilt zu grob**: nur 2 Themen für eine breite 16-Minuten-Debatte. |
| C2 Delta, Prompt nachgeschärft | 5327 | 6 | 5 Themen statt 2, bei gleicher Laufzeit. Der Mangel lag am Prompt, nicht am Verfahren. |

Der Veraltet-Hinweis wirkt also — und zwar nicht, weil er Anchoring verhindert
(das trat nicht auf), sondern weil er dem Modell ausdrücklich erlaubt,
umzustellen und zu streichen. Arm A verlor ohne diese Erlaubnis Inhalte.

## 3. Laufzeit: hier entscheidet sich der Live-Betrieb

| Arm | Prompt-Tokens gesamt | letzter Zyklus | Ausgabe-Tokens | Latenz Median | Latenz Verlauf |
|---|---|---|---|---|---|
| A | 17.402 | 1818 | 13.567 | 33,5 s | 10,8 s → 39,9 s |
| B | 21.279 | 2136 | 15.254 | 38,1 s | 12,9 s → 44,1 s |
| C | 9.186 | 625 | 1.540 | **4,4 s** | 5,5 s → 3,1 s |

Das ist der eigentliche Befund. Bei A und B wächst der Prompt linear mit der
Sitzungsdauer (537 → 2325 Tokens), die Latenz entsprechend mit: nach 16 Minuten
schon über 40 s je Lauf. Hochgerechnet auf eine Stunde (≈40 Zyklen) läge die
Laufzeit deutlich über dem 90-Sekunden-Auslöseintervall — das Protokoll geriete
dauerhaft in Rückstand.

Arm C bleibt **konstant** bei 3–6 s, unabhängig davon, an welcher Stelle der
Sitzung er läuft, und braucht rund ein Neuntel der Ausgabe-Tokens.

## 4. Schlussfolgerung

Beides stimmt gleichzeitig, und keines der beiden Verfahren gewinnt auf ganzer
Linie:

- **Der Volldurchlauf mit Veraltet-Hinweis liefert das bessere Protokoll.**
- **Die Delta-Strategie ist das einzige Verfahren, das über eine lange Sitzung
  nicht langsamer wird.** Ihr Qualitätsnachteil ist zum Teil Prompt-Sache und
  ließ sich in einem Durchgang halbieren.

Umgesetzt ist deshalb beides plus eine Kombination:

| Fall | Empfehlung |
|---|---|
| Besprechung bis ~30 Min. | `rewrite` — beste Fassung, Latenz noch tragbar |
| Lange Besprechung | `delta` + Abschlusslauf — konstante Latenz während der Sitzung, Politur einmal am Ende |
| Brainstorming | `rewrite` — ein Durchgang am Ende, kein Live-Zwang |

Der Abschlusslauf (`FINAL_PASS`) ist der Kompromiss: die Struktur wächst
während der Aufnahme billig per Delta, und genau ein Durchgang am Ende bringt
sie auf das sprachliche Niveau des Volldurchlaufs, ohne dass die Latenz
während der Sitzung darunter leidet.

## 5. Nebenbefund: Denkmodelle

Der erste Durchlauf war unbrauchbar — Arm B lieferte in 2 von 5 Zyklen ein
**leeres** Protokoll, Arm C in 3 von 5 Zyklen **null** gültige Operationen.
Ursache: `qwen3.5:9b` verbraucht per Default den Großteil seines Budgets im
`reasoning`-Feld und gibt dann gelegentlich leeren `content` zurück.

`reasoning_effort: "none"` auf dem OpenAI-Pfad schaltet das ab (36 s → 7 s).
`chat_template_kwargs.enable_thinking=false` wirkt **nicht**; `think: false`
funktioniert nur auf Ollamas nativem `/api/chat`.

## Nachstellen

```bash
python3 run_comparison.py --model qwen3.5:9b --transcript out/full.json --window 90
python3 evaluate.py --transcript out/full.json --window 90   # nur Metrik, ohne LLM
python3 variant_c.py                                          # Prompt-Variante für Arm C
```

---

# Nachtrag: Lauf gegen echtes Mikrofon-Audio (2026-09-02)

Erster vollständiger Durchlauf durch die echte Pipeline (Whisper und
Sprachmodell lokal), nicht mehr nur über die Vergleichs-Harness.

Material: 10:57 Mikrofon-Aufnahme, eine Person spricht frei ein Konzept ein —
genau der Brainstorming-Fall, und inhaltlich fortschreitend, weil die Idee sich
im Reden entwickelt.

## Ergebnis

Beide Strategien liefern ein brauchbares Konzeptdokument. Kein Ausfall, keine
kaputte Struktur, keine leeren Läufe.

| | rewrite | delta |
|---|---|---|
| Umfang | 4235 Zeichen | 2552 Zeichen |
| Prompt-Tokens letzter Lauf | 1552 | 897 |
| Durchsatz gesamt | 3,7x Echtzeit | 5,6x Echtzeit |
| Gliederung | folgt der Vorgabe des Ausgabetyps | selbst gebildete Themen |

## Der eigentliche Befund: Gliederungstreue

`brainstorming.md` gibt eine feste Zielgliederung vor (Ziel, Kernidee,
Bausteine, Randbedingungen, offene Fragen). **rewrite hält sich daran, delta
nicht** — delta bildet seine Abschnitte aus dem Gespräch heraus und kann ein
vorgegebenes Skelett prinzipbedingt nicht treffen, weil es nie das ganze
Dokument sieht.

Das ist kein Prompt-Mangel, sondern folgt aus dem Verfahren, und es schärft
die Empfehlung:

- **Fester Zielaufbau gewünscht** (Brainstorming, Konzept, jede Vorlage)
  → `rewrite`. Der Live-Zwang fehlt hier ohnehin, ein Durchgang am Ende reicht.
- **Offener Verlauf mit unbekannten Themen** (Besprechung) → `delta`. Dass die
  Themen organisch entstehen, ist hier ein Vorteil, und die Latenz bleibt flach.

## Anchoring, zweiter Blick

Auch hier kein Zusammenbruch: Spät Gesprochenes (Mobilgeräte, Geräteaudio,
das Gewichtungsproblem selbst) steht im Endprotokoll. Beim `meeting`-Lauf über
dasselbe Material war allerdings sichtbar, dass Abschnitt 1 über die Läufe 2
bis 4 praktisch unverändert stehen blieb — die Tendenz existiert also, sie
richtet nur in 11 Minuten noch keinen Schaden an.

Verloren gingen in beiden Fällen nur Detailangaben (`Voxtral` bei rewrite,
`Handy` bei delta, die Zeichenzahl-Konfiguration bei beiden).

## Einmalige Zusammenfassung mit GLM 5.3 bei Mistral (2026-09-23)

Für die Nachbearbeitung: Wie lange braucht eine Zusammenfassung über das ganze
Transkript, und kommt sie bei langen Terminen noch vollständig an?
Skript: `summary_length.py` (die Rohdaten in `out/` sind nicht versioniert, wie alle Messläufe).

Modell `zai-glm-5-3` über den EU-Endpunkt `https://api.eu.mistral.ai`. Je
Länge eine Anfrage; drei erfundene Aussagen stecken am Anfang, in der Mitte
und am Ende und müssen im Ergebnis auftauchen. Material ist das
Talkshow-Transkript, für größere Längen wiederholt - das misst Dauer und
Kontext, nicht die Qualität.

| Wörter | ≈ Termin | Prompt-Tokens | `low`: Dauer / Ausgabe | `high`: Dauer / Ausgabe | Anfang, Mitte, Ende |
|---|---|---|---|---|---|
| 1.000 | 6 min | 1.677 | 4,6 s / 827 | 8,6 s / 1.468 | alle drei, beide Stufen |
| 5.000 | 30 min | 7.659 | 7,4 s / 1.445 | 8,7 s / 1.585 | alle drei |
| 10.000 | 1 h | 15.269 | 9,5 s / 1.765 | 14,3 s / 2.652 | alle drei |
| 30.000 | 3 h | 45.434 | 7,7 s / 1.102 | 13,0 s / 2.054 | alle drei |

- **Die Länge ist kein Problem.** Auch drei Stunden Transkript gehen in einem
  Lauf durch, in unter 15 s, und die Mitte bleibt drin. Ein zweistufiges
  Zusammenfassen braucht es für dieses Modell nicht. Das feste Timeout von
  300 s reicht mit großem Abstand.
- **Rate-Limit: 100.000 Tokens je Minute.** Der Lauf über 30.000 Wörter kostet
  davon rund 40.000 (`x-ratelimit-tokens-query-cost`). Zwei lange Termine
  kurz hintereinander liefen einmal in ein 429. Die App muss es abfangen und
  nach einer Pause wiederholen.
- **Denken steuern:** `reasoning_effort` kennt `low`, `high` und `max`, nicht
  `none`. Mit `low` kommt kein Denkanteil, `content` ist ein einfacher Text.
  Mit `high` (und ohne Angabe) ist `content` eine **Liste** aus einem
  `thinking`- und einem `text`-Block. Der Client muss beides lesen können.
- **Kosten** (EU-Endpunkt, 1,1 × Listenpreis): eine Stunde rund 3 ct, drei
  Stunden rund 8 ct.

## Modelle über OpenRouter mit ZDR (2026-09-23)

Material: das Talkshow-Transkript (rund 3.000 Wörter, 6.800 Prompt-Tokens),
Prompt aus `app/summary.py`. Alle OpenRouter-Anfragen mit `provider.zdr: true`
und `data_collection: "deny"`.

**Zusammenfassung nach dem Termin**

| Modell, Weg | Einstellung | Dauer | Ausgabe-Tokens |
|---|---|---|---|
| GLM 5.3 Flash, OpenRouter | ohne Denkbremse | 349 s / 250 s | 23.141 / 26.741 |
| GLM 5.3 Flash, OpenRouter | `reasoning.effort: low` | 64–104 s | ~2.500 |
| GLM 5.3 Flash, OpenRouter | `low`, `sort: latency` | 31 s | 2.241 |
| GLM 5.3, Mistral EU | `reasoning_effort: low` | 11 s | 2.363 |
| GLM 5.3, Mistral EU | `reasoning_effort: high` | 28 s | 6.096 |

- **Mit ZDR ist Flash nicht schnell.** Jede Anfrage landete bei Parasail,
  auch mit dem Filter auf fp8/bf16 oder nach Durchsatz sortiert. Ein Anbieter
  (Modal) lieferte einmal gar keinen Text. Das „schnelle, günstige“ Modell
  braucht damit länger als GLM 5.3 in der EU. Der Preisunterschied ist
  absolut klein (unter 1 ct gegen rund 2 ct je Lauf).
- **Ohne Denkbremse denkt Flash sich tot**: 23.000 Ausgabe-Tokens für ein
  Dokument von 15.000 Zeichen.
- Qualität grob gesichtet: alle drei brauchbar. Flash mit einem verunglückten
  Satz, GLM 5.3 `high` mit einem eigenen Hinweis auf verstümmelte Namen im
  Transkript.

**Live-Protokoll** (`rewrite`, vier Läufe à 1.200 Zeichen neuer Text)

| Modell, Weg | Dauer je Lauf |
|---|---|
| Gemma 4 26B A4B, OpenRouter, `sort: latency` | 5,4 / 7,6 / 10,2 / 7,4 s |
| GLM 5.3 Flash, OpenRouter, `low` | 19,8 / 38,1 / 8,2 / 10,7 s |
| Ministral 8B, Mistral EU | 6,6 / 9,0 / 16,5 / 20,0 s |

Gemma ist am schnellsten und bleibt flach, das Ergebnis ist brauchbar
gegliedert. Es ist jetzt das Live-Modell, Nemo bleibt Ausweichweg.

## Sprechererkennung mit Voxtral `diarize` (2026-09-23)

Skript: `diarize_probe.py`. Material: ein Podcast von 20 Minuten mit mehreren
Sprechern (liegt in `audio/`, nicht versioniert) und die eigene
Mikrofonaufnahme von 11 Minuten mit einem Sprecher. Endpunkt
`api.eu.mistral.ai`, Modell `voxtral-mini-latest`.

- **Antwortformat:** `segments` mit `start`, `end`, `text`, `type`
  (`transcription_segment`) und `speaker_id` (`speaker_1`, `speaker_2`, …).
  Die Segmente sind kurz, im Median 1,6 s. Überlappungen gibt es keine: Wer
  dazwischenredet, steht als eigenes Segment in der Folge.
- **`diarize` verlangt Zeitstempel:** ohne `timestamp_granularities=segment`
  kommt HTTP 422.
- **`language` und Zeitstempel vertragen sich** – entgegen der Doku. Mit
  `language=de` dieselben Sprecher, dieselbe Segmentzahl. Auch ohne
  `diarize` geht `language` zusammen mit Zeitstempeln.
- **`context_bias` geht mit `diarize`**, aber nur mit Einzelwörtern; ein
  Eintrag mit Leerzeichen gibt HTTP 400 (3051).
- **Sprecher:** Im Podcast sechs Kennungen, zwei mit dem Großteil der
  Sprechzeit (526 s und 381 s), drei mit 18–26 s, eine mit einem einzigen
  Satz. Die seltenen lesen sich wie echte Mitsprecher mit kurzen Einwürfen.
  Ob sie stimmen, lässt sich nur mit dem Ohr prüfen – genau dafür ist die
  Zuordnung mit Hörproben da. In der Aufnahme mit einem Sprecher erfindet es
  **keine** weiteren (`speaker_1` für alle 94 Segmente).
- **Dauer:** 14–31 s für 20 Minuten Podcast, 9 s für 11 Minuten. Eine Stunde
  läge bei rund einer Minute.
- **Kosten:** abgerechnet nach `prompt_audio_seconds`, $0,003 je Minute, also
  rund 18 ct je Stunde.
