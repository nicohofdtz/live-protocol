"""Wer die Anwendung benutzt.

Ohne weitere Einrichtung gibt es genau einen, impliziten Benutzer - am
eigenen Rechner wie auf dem Server. Mehrere Benutzer gibt es nur hinter einem
Proxy, der die Anmeldung übernimmt und den Benutzer als Kopfzeile Remote-User
weiterreicht (etwa Authelia oder oauth2-proxy, `AUTH_PROXY=true`). Die
Anwendung selbst prüft keine Passwörter: Ein eigener Anmeldeweg wäre ein
zweiter, schlechter gepflegter neben dem des Proxys.
"""

from __future__ import annotations

# Der Benutzer ohne Proxy. Unter diesem Namen liegen Einstellungen und
# gespeicherte Sitzungen ("Meine Sitzungen") auch auf dem Server.
IMPLICIT_USER = "local"

# Gruppen beim Proxy, die gesperrte Transkriptions-Dienste freischalten:
# "lp-<dienst-id>", etwa "lp-voxtral".
ENGINE_GROUP_PREFIX = "lp-"


def engines_from_groups(header: str | None) -> list[str]:
    """Freigeschaltete Dienste aus der Remote-Groups-Kopfzeile."""
    groups = [g.strip() for g in (header or "").split(",")]
    return [g[len(ENGINE_GROUP_PREFIX):] for g in groups
            if g.startswith(ENGINE_GROUP_PREFIX) and len(g) > len(ENGINE_GROUP_PREFIX)]
