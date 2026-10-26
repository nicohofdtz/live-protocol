---
id: meeting
name: Besprechungsprotokoll
short_name: Protokoll
description: Laufendes Protokoll einer Besprechung mit mehreren Teilnehmern
default_strategy: rewrite
# Bausteine für die Zusammenfassung nach dem Termin, zum Ankreuzen.
summary_blocks:
  - id: kurzfassung
    label: Kurzfassung oben
    default: true
    instruction: >-
      Beginne mit einer Kurzfassung in höchstens drei Sätzen: worum es ging und
      was dabei herauskam.
  - id: entscheidungen
    label: Entscheidungen
    default: true
    instruction: >-
      Führe gefasste Beschlüsse in einem eigenen Abschnitt „Entscheidungen“
      auf. Nur, was ausdrücklich beschlossen wurde, keine Tendenzen.
  - id: aufgaben
    label: Aufgabentabelle
    default: true
    instruction: >-
      Schließe mit einer Tabelle „Aufgaben“ mit den Spalten Was, Wer und Bis
      wann. Nur Aufgaben, die ausdrücklich verabredet wurden. Ist keine Person
      genannt, steht bei Wer „offen“; ist kein Termin genannt, bleibt Bis wann
      leer.
  - id: offene_fragen
    label: Offene Fragen
    default: true
    instruction: >-
      Sammle offen gebliebene Fragen in einem eigenen Abschnitt.
---
Gliedere nach Themen. Halte je Thema fest: Kernaussagen, vertretene Positionen
und strittige Punkte. Erfasse außerdem gefasste Beschlüsse, verabredete
Aufgaben (mit Zuständigkeit, falls genannt) und offen gebliebene Fragen.

Stelle keine Zusammenhänge her, die im Gespräch nicht ausdrücklich
hergestellt wurden - zwei Punkte, die nacheinander besprochen werden, hängen
nicht deshalb zusammen.

Keine Sprecherzuordnung erfinden. Sind Sprecher nicht sicher unterscheidbar,
formuliere unpersönlich ("es wird eingewandt", "eine Position lautet").
