"""Erzeugt Programm- und Seitensymbol aus einer Beschreibung.

Eine Quelle, vier Ausgaben: SVG für die Webseite, PNG für Kachel und
Lesezeichen, ICNS für macOS, ICO für Windows. Die Geometrie steht nur hier -
ein von Hand gepflegtes SVG neben handgezeichneten PNGs wäre nach der zweiten
Änderung auseinandergelaufen.

Aufruf:  uv run --with pillow python build/make_icons.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

GRUEN = "#2b6b52"      # --accent der Oberfläche
WEISS = "#ffffff"
KANTE = 1024
RADIUS = 232           # abgerundetes Quadrat, an die Plattformform angelehnt

# Links Ton, rechts Text: drei stehende Balken wie der Pegelanzeiger, daneben
# drei liegende Zeilen. Das Bild sagt, was das Programm tut - aus Gesprochenem
# wird Geschriebenes - und die beiden Gruppen unterscheiden sich in der
# Richtung, was auch dann noch trägt, wenn bei 16 Pixeln nichts mehr scharf ist.
DICKE, ENDRUND = 92, 46
TON_HOEHEN = (360, 620, 460)
TEXT_BREITEN = (300, 240, 176)


def formen() -> list[tuple[float, float, float, float]]:
    mitte = KANTE / 2
    kaesten = []

    luecke = 52
    x = 140
    for hoehe in TON_HOEHEN:
        kaesten.append((x, mitte - hoehe / 2, x + DICKE, mitte + hoehe / 2))
        x += DICKE + luecke

    zeilen_hoehe = len(TEXT_BREITEN) * DICKE + (len(TEXT_BREITEN) - 1) * luecke
    y = mitte - zeilen_hoehe / 2
    links = 596
    for breite in TEXT_BREITEN:
        kaesten.append((links, y, links + breite, y + DICKE))
        y += DICKE + luecke
    return kaesten


def als_svg() -> str:
    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {KANTE} {KANTE}">',
        f'  <rect width="{KANTE}" height="{KANTE}" rx="{RADIUS}" fill="{GRUEN}"/>',
    ]
    for x0, y0, x1, y1 in formen():
        teile.append(
            f'  <rect x="{x0:g}" y="{y0:g}" width="{x1 - x0:g}" height="{y1 - y0:g}"'
            f' rx="{ENDRUND}" fill="{WEISS}"/>'
        )
    teile.append("</svg>")
    return "\n".join(teile) + "\n"


def als_bild(kante: int) -> Image.Image:
    """Vierfach gezeichnet und verkleinert - Pillow kantenglättet nicht selbst."""
    gross = KANTE * 2
    faktor = gross / KANTE
    bild = Image.new("RGBA", (gross, gross), (0, 0, 0, 0))
    stift = ImageDraw.Draw(bild)
    stift.rounded_rectangle((0, 0, gross - 1, gross - 1),
                            radius=RADIUS * faktor, fill=GRUEN)
    for x0, y0, x1, y1 in formen():
        stift.rounded_rectangle((x0 * faktor, y0 * faktor, x1 * faktor, y1 * faktor),
                                radius=ENDRUND * faktor, fill=WEISS)
    return bild.resize((kante, kante), Image.LANCZOS)


def main() -> None:
    (STATIC / "icon.svg").write_text(als_svg(), encoding="utf-8")
    als_bild(180).save(STATIC / "icon-180.png")      # Kachel auf dem Telefon
    als_bild(512).save(STATIC / "icon-512.png")

    als_bild(256).save(ROOT / "build" / "icon.ico",
                       sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory() as tmp:
            satz = Path(tmp) / "icon.iconset"
            satz.mkdir()
            for kante in (16, 32, 128, 256, 512):
                als_bild(kante).save(satz / f"icon_{kante}x{kante}.png")
                als_bild(kante * 2).save(satz / f"icon_{kante}x{kante}@2x.png")
            subprocess.run(["iconutil", "-c", "icns", str(satz),
                            "-o", str(ROOT / "build" / "icon.icns")], check=True)
    elif not (ROOT / "build" / "icon.icns").exists():
        print("Hinweis: icon.icns wird nur unter macOS erzeugt (iconutil).")

    for pfad in ("static/icon.svg", "static/icon-180.png", "static/icon-512.png",
                 "build/icon.ico", "build/icon.icns"):
        ziel = ROOT / pfad
        if ziel.exists():
            print(f"{pfad:28} {ziel.stat().st_size:>8} Bytes")


if __name__ == "__main__":
    main()
