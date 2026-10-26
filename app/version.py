"""Programmversion und Versionsvergleich.

Die Zahl steht hier und in `pyproject.toml`; ein Test hält beide zusammen.
"""

from __future__ import annotations

__version__ = "0.7.0"


def parse(version: str) -> tuple[int, ...]:
    """"1.2.3" -> (1, 2, 3). Unbrauchbares wird zu (0,), gilt also als aelter."""
    teile = []
    for stueck in version.strip().lstrip("v").split("."):
        ziffern = ""
        for zeichen in stueck:
            if not zeichen.isdigit():
                break
            ziffern += zeichen
        if not ziffern:
            break
        teile.append(int(ziffern))
    return tuple(teile) if teile else (0,)


def is_newer(angeboten: str, laufend: str = __version__) -> bool:
    return parse(angeboten) > parse(laufend)
