"""Startet die Anwendung als eigenständiges Programm.

Kümmert sich um whisper-server, den Webdienst und das Fenster. Chromium-
basierte Browser öffnen mit `--app=` ein Fenster ohne Adressleiste und Tabs,
das wie ein eigenes Programm aussieht. Unter Windows greift das immer (Edge
ist vorinstalliert), unter macOS nur mit Chrome, Edge oder Brave - Safari
kann es nicht, dort wird es ein normaler Tab.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

logger = logging.getLogger("live-protocol.launch")

CHROMIUM_CANDIDATES = {
    "darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ],
    "win32": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "linux": ["google-chrome", "chromium", "chromium-browser", "microsoft-edge"],
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(host: str, port: int, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.25)
    return False


def find_chromium() -> str | None:
    for candidate in CHROMIUM_CANDIDATES.get(sys.platform, []):
        if os.path.isabs(candidate):
            if Path(candidate).exists():
                return candidate
        elif (found := shutil.which(candidate)):
            return found
    return None


def open_window(url: str, profile_dir: Path) -> subprocess.Popen | None:
    """Rahmenloses Fenster, wenn möglich. Eigenes Profil, damit das Fenster ein
    eigener Prozess ist - dann bedeutet Schließen auch Beenden."""
    browser = find_chromium()
    if browser is None:
        logger.info("Kein Chromium-Browser gefunden, öffne im Standardbrowser")
        webbrowser.open(url)
        return None
    return subprocess.Popen([
        browser,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Ohne das blendet Chrome im App-Fenster ungefragt das
        # Uebersetzen-Fenster ein.
        "--disable-features=Translate",
        "--disable-translate",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def laufende_instanz(sperre: Path) -> str | None:
    """Adresse einer bereits laufenden Instanz, sonst None.

    Wer das Programmsymbol ein zweites Mal anklickt, will zurück zu seinem
    Fenster und nicht ein zweites daneben. Die Sperrdatei hält die Adresse der
    laufenden Instanz; antwortet dort niemand mehr (Absturz, harter Neustart),
    ist sie wertlos und wird überschrieben."""
    try:
        port = int(sperre.read_text().strip())
    except (OSError, ValueError):
        return None
    with socket.socket() as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) != 0:
            return None
    return f"http://127.0.0.1:{port}/"


def start_whisper(model_path: Path, port: int) -> subprocess.Popen | None:
    from app.paths import whisper_server_binary

    binary = whisper_server_binary() or shutil.which("whisper-server")
    if binary is None:
        logger.warning("whisper-server nicht gefunden - lokale Transkription "
                       "steht nicht zur Verfügung")
        return None
    # -sow: an Wortgrenzen umbrechen statt an Tokengrenzen (sonst "Whis per").
    return subprocess.Popen(
        [str(binary), "-m", str(model_path), "-l", "de",
         "--host", "127.0.0.1", "--port", str(port), "-sow"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app import models
    from app.paths import data_dir, sessions_dir

    sperre = data_dir() / "instanz.port"
    if (vorhanden := laufende_instanz(sperre)) is not None:
        # Kein zweiter Dienst und kein zweites Fenster: Das vorhandene wird
        # nach vorn geholt, danach ist hier nichts mehr zu tun.
        logger.info("Läuft bereits auf %s", vorhanden)
        open_window(vorhanden, data_dir() / "browser-profile")
        return 0

    whisper_port = free_port()
    web_port = free_port()

    # Die Engine "Whisper (lokal)" folgt dieser Adresse - der Port ist frei
    # gewählt, nicht der feste 8081 aus dem Quellbetrieb.
    os.environ.setdefault("WHISPER_URL", f"http://127.0.0.1:{whisper_port}")
    os.environ.setdefault("SESSIONS_DIR", str(sessions_dir()))
    os.environ.setdefault("DEPLOYMENT_MODE", "desktop")

    whisper = None
    if models.is_present():
        whisper = start_whisper(models.DEFAULT.path, whisper_port)
    else:
        # Das Modell fehlt noch. Der Dienst startet trotzdem, damit das
        # Fenster den Download anzeigen kann statt einer leeren Seite.
        logger.info("Modell fehlt - wird beim ersten Start im Fenster geladen")

    import uvicorn

    from app.main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=web_port, log_level="warning")
    server = uvicorn.Server(config)

    import threading

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_for("127.0.0.1", web_port, timeout=30):
        logger.error("Der Webdienst ist nicht gestartet")
        return 1

    sperre.parent.mkdir(parents=True, exist_ok=True)
    sperre.write_text(str(web_port))

    url = f"http://127.0.0.1:{web_port}/"
    window = open_window(url, data_dir() / "browser-profile")
    logger.info("Läuft auf %s", url)

    try:
        if window is not None:
            window.wait()          # Fenster zu = Programm zu
        else:
            while thread.is_alive():
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        if whisper is not None:
            whisper.terminate()
        sperre.unlink(missing_ok=True)
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
