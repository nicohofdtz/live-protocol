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
        # Lizenztexte gehören mit in die App - whisper.cpp und lamejs stecken drin.
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


def paket_lizenzen() -> Path:
    """Lizenztexte aller Python-Pakete, die tatsächlich in der App landen.

    PyInstaller packt die *.dist-info nur für wenige Pakete mit. Diese Datei
    sammelt deshalb Lizenzangabe und Lizenzdateien jedes Pakets, aus dem ein
    Modul in der App steckt, plus PyInstaller selbst (Bootloader)."""
    import re
    from importlib import metadata

    namen = {eintrag[0].replace("\\", "/").split("/")[0].split(".")[0]
             for eintrag in (*a.pure, *a.binaries)}
    zuordnung = metadata.packages_distributions()
    pakete = {d for n in namen for d in zuordnung.get(n, [])} | {"pyinstaller"}
    muster = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)
    teile = ["Lizenzen der Python-Pakete in dieser App (automatisch beim Bauen erzeugt)", ""]
    for name in sorted(pakete, key=str.lower):
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        meta = dist.metadata
        lizenz = (meta.get("License-Expression") or "").strip()
        kurz = (meta.get("License") or "").strip()
        if not lizenz and kurz and "\n" not in kurz and len(kurz) < 80:
            lizenz = kurz
        if not lizenz:
            lizenz = "; ".join(c.split(" :: ")[-1] for c in meta.get_all("Classifier") or []
                               if c.startswith("License ::"))
        teile += ["=" * 78, f"{meta['Name']} {dist.version}", f"Lizenz: {lizenz or 'siehe Text'}", ""]
        for datei in sorted(dist.files or [], key=str):
            if muster.search(datei.name):
                text = dist.locate_file(datei).read_text(encoding="utf-8", errors="replace")
                teile += [f"--- {datei}", text.strip(), ""]
    ziel = Path(workpath) / "python-pakete.txt"
    ziel.write_text("\n".join(teile) + "\n", encoding="utf-8")
    return ziel


# Die Laufzeit selbst (CPython samt OpenSSL & Co.) steht in licenses/python/.
lizenzen = [("licenses/python-pakete.txt", str(paket_lizenzen()), "DATA")]

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="live-protocol",
    console=False,          # kein Terminalfenster; Fortschritt zeigt die Oberfläche
    icon=symbol,
)
coll = COLLECT(exe, a.binaries, a.datas + lizenzen, name="live-protocol")

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
            # Dasselbe für den Systemton: Das Fenster ist ein Kindprozess
            # dieser App, macOS rechnet ihm die Aufnahme zu.
            "NSAudioCaptureUsageDescription":
                "Für die Aufzeichnung des Systemtons von Besprechungen.",
        },
    )
