"""Ablageorte, plattformgerecht.

Im ausgelieferten Programm darf nichts neben der Anwendung liegen: unter
macOS steckt sie in einem signierten (bzw. quarantänebehafteten) Bündel,
unter Windows oft in `Program Files` ohne Schreibrecht.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "live-protocol"


def data_dir() -> Path:
    """Ort für Modelle, Sitzungen und lokale Konfiguration."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sessions_dir() -> Path:
    path = data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    return data_dir() / "config.json"


def bundled_dir() -> Path:
    """Verzeichnis der mitgelieferten Dateien.

    PyInstaller entpackt in `sys._MEIPASS`; im Quellbetrieb ist es das
    Projektverzeichnis.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def whisper_server_binary() -> Path | None:
    """Mitgelieferte whisper-server-Binärdatei, falls vorhanden.

    Im gebündelten Programm liegt sie unter `bin/`, im Quellbetrieb nach einem
    lokalen Build unter `build/bin/`. Findet sich keine, greift der Aufrufer
    auf ein whisper-server aus dem PATH zurück.
    """
    name = "whisper-server.exe" if os.name == "nt" else "whisper-server"
    root = bundled_dir()
    for candidate in (root / "bin" / name, root / "build" / "bin" / name):
        if candidate.exists():
            return candidate
    return None
