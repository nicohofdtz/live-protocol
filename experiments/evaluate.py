"""Wertet gespeicherte Endprotokolle neu aus - ohne erneute LLM-Aufrufe.

    python3 evaluate.py --transcript out/excerpt-5min.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

import metrics
from run_comparison import load_cycles

OUT = Path(__file__).resolve().parent / "out"


def main(args: argparse.Namespace) -> None:
    cycles = load_cycles(Path(args.transcript), args.window)
    texts = [c["text"] for c in cycles]

    terms = metrics.distinctive_terms(texts, top_k=args.top_k)
    print(f"{len(cycles)} Zyklen; kennzeichnende Begriffe je Zyklus:")
    for i, t in enumerate(terms):
        print(f"  Z{i}: {len(t):2d} -> {', '.join(t[:8])}")
    print()

    header = "| Arm | " + " | ".join(f"Z{i}" for i in range(len(cycles))) + " | Spanne | Zeichen |"
    print(header)
    print("|" + "---|" * (len(cycles) + 3))
    for arm in args.arms:
        path = OUT / f"arm{arm}-final.md"
        if not path.exists():
            continue
        final = path.read_text(encoding="utf-8")
        cov = [metrics.coverage(t, final) for t in terms]
        print(f"| {arm} | " + " | ".join(f"{v:.2f}" for v in cov) +
              f" | {max(cov) - min(cov):.2f} | {len(final)} |")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--transcript", default=str(OUT / "excerpt-5min.json"))
    p.add_argument("--window", type=float, default=60.0)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--arms", nargs="+", default=["A", "B", "C"])
    main(p.parse_args())
