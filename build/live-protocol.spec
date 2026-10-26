# PyInstaller-Beschreibung. Aufruf über build/build.sh bzw. die CI.
#
# Das Whisper-Modell steckt bewusst NICHT hier drin: 574 MB plattformunabhängige
# Daten in jedem Installer wären reine Bandbreitenverschwendung. Es wird beim
# ersten Start nachgeladen.

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
BIN = ROOT / "build" / "bin"

# Erzeugt von build/make_icons.py. Windows will .ico, macOS .icns - dasselbe
# Bild in zwei Formaten, nicht zwei Symbole.
SYMBOL = ROOT / "build" / ("icon.ico" if sys.platform == "win32" else "icon.icns")
symbol = str(SYMBOL) if SYMBOL.exists() else None

binaries = []
for name in ("whisper-server", "whisper-server.exe"):
    candidate = BIN / name
    if candidate.exists():
        binaries.append((str(candidate), "bin"))

# python-docx braucht seine Vorlage (default.docx) zur Laufzeit.
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    [str(ROOT / "launch.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=[
        (str(ROOT / "static"), "static"),
        (str(ROOT / "output_types"), "output_types"),
        # Lizenztexte gehören mit ins Bündel - whisper.cpp und lamejs stecken drin.
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "THIRD_PARTY.md"), "."),
        (str(ROOT / "licenses"), "licenses"),
        *collect_data_files("docx"),
    ],
    hiddenimports=["uvicorn.logging", "uvicorn.protocols", "uvicorn.lifespan"],
    hookspath=[],
    excludes=["tkinter", "matplotlib", "numpy", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="live-protocol",
    console=False,          # kein Terminalfenster; Fortschritt zeigt die Oberfläche
    icon=symbol,
)
coll = COLLECT(exe, a.binaries, a.datas, name="live-protocol")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Live-Protokoll.app",
        icon=symbol,
        bundle_identifier="io.github.nicohofdtz.live-protocol",
        info_plist={
            "CFBundleName": "Live-Protokoll",
            "CFBundleDisplayName": "Live-Protokoll",
            "NSHighResolutionCapable": True,
            # Sonst startet ein Klick auf das Symbol eine zweite Instanz - mit
            # eigenem Dienst und eigenem Fenster daneben.
            "LSMultipleInstancesProhibited": True,
            # Ohne diesen Text verweigert macOS den Mikrofonzugriff wortlos.
            "NSMicrophoneUsageDescription":
                "Für die Aufzeichnung und Transkription von Besprechungen.",
        },
    )
