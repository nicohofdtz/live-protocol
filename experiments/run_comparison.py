"""Vergleich der drei Fortschreibungsstrategien auf einem echten Transkript.

Simuliert den Live-Betrieb: das Transkript wird in Zeitfenster zerlegt, und
pro Fenster läuft ein Fortschreibungszyklus - genau wie später in der App.

Aufruf:
    python3 run_comparison.py --model qwen3.5:9b
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import arms
import metrics
from llm import LLMError

OUT = Path(__file__).resolve().parent / "out"


def load_cycles(json_path: Path, window_seconds: float) -> list[dict]:
    """Whisper-JSON in Zeitfenster zerlegen."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    segments = data["transcription"]

    def to_seconds(ts: str) -> float:
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000

    cycles: list[dict] = []
    for seg in segments:
        start = to_seconds(seg["timestamps"]["from"])
        idx = int(start // window_seconds)
        while len(cycles) <= idx:
            n = len(cycles)
            cycles.append(
                {"index": n, "start": n * window_seconds, "texts": []}
            )
        cycles[idx]["texts"].append(seg["text"].strip())

    for c in cycles:
        c["text"] = " ".join(c["texts"]).strip()
    return [c for c in cycles if c["text"]]


def run(args: argparse.Namespace) -> None:
    OUT.mkdir(exist_ok=True)
    cycles = load_cycles(Path(args.transcript), args.window)
    cycle_texts = [c["text"] for c in cycles]
    print(f"{len(cycles)} Zyklen a {args.window:.0f}s, "
          f"{sum(len(t) for t in cycle_texts)} Zeichen Transkript\n")

    kw = dict(model=args.model, base_url=args.base_url, api_key=args.api_key)
    results: dict[str, dict] = {}

    # -- Arme A und B: Volldurchlauf ------------------------------------
    for arm, system in (("A", arms.SYSTEM_A), ("B", arms.SYSTEM_B)):
        print(f"--- Arm {arm} ({'naiv' if arm == 'A' else 'Veraltet-Hinweis'}) ---")
        protocol = ""
        per_cycle = []
        for c in cycles:
            try:
                protocol, usage, latency = arms.run_rewrite_arm(
                    system, protocol, c["text"], **kw
                )
            except LLMError as exc:
                raise SystemExit(f"Arm {arm}, Zyklus {c['index']}: {exc}")
            per_cycle.append({
                "cycle": c["index"],
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "latency_s": round(latency, 2),
                "protocol_chars": len(protocol),
            })
            print(f"  Zyklus {c['index']}: {latency:5.1f}s  "
                  f"prompt={usage.get('prompt_tokens'):>5}  "
                  f"out={usage.get('completion_tokens'):>4}  "
                  f"Protokoll={len(protocol):>5} Zeichen")
            (OUT / f"arm{arm}-cycle{c['index']}.md").write_text(protocol, encoding="utf-8")
        (OUT / f"arm{arm}-final.md").write_text(protocol, encoding="utf-8")
        results[arm] = {"per_cycle": per_cycle, "final": protocol}
        print()

    # -- Arm C: Delta ----------------------------------------------------
    print("--- Arm C (Delta/Append) ---")
    state = arms.ProtocolState()
    per_cycle = []
    for c in cycles:
        try:
            usage, latency, n_ops = arms.run_delta_arm(state, c["text"], c["index"], **kw)
        except LLMError as exc:
            raise SystemExit(f"Arm C, Zyklus {c['index']}: {exc}")
        rendered = state.render()
        per_cycle.append({
            "cycle": c["index"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "latency_s": round(latency, 2),
            "protocol_chars": len(rendered),
            "ops": n_ops,
            "topics": len(state.topics),
        })
        print(f"  Zyklus {c['index']}: {latency:5.1f}s  "
              f"prompt={usage.get('prompt_tokens'):>5}  "
              f"out={usage.get('completion_tokens'):>4}  "
              f"ops={n_ops:>2}  Themen={len(state.topics):>2}  "
              f"Protokoll={len(rendered):>5} Zeichen")
        (OUT / f"armC-cycle{c['index']}.md").write_text(rendered, encoding="utf-8")
    final_c = state.render()
    (OUT / "armC-final.md").write_text(final_c, encoding="utf-8")
    (OUT / "armC-state.json").write_text(
        json.dumps({"topics": state.topics, "decisions": state.decisions,
                    "actions": state.actions, "questions": state.questions},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    results["C"] = {"per_cycle": per_cycle, "final": final_c,
                    "invalid_ops": state.invalid_ops}
    print()

    # -- Auswertung ------------------------------------------------------
    report = ["# Vergleich der Fortschreibungsstrategien", "",
              f"Modell: `{args.model}` | Zyklen: {len(cycles)} a {args.window:.0f}s "
              f"| Transkript: {sum(len(t) for t in cycle_texts)} Zeichen", ""]

    report.append("## Gewichtung: Abdeckung der kennzeichnenden Begriffe je Zyklus")
    report.append("")
    header = "| Arm | " + " | ".join(f"Z{c['index']}" for c in cycles) + " | Spanne |"
    report.append(header)
    report.append("|" + "---|" * (len(cycles) + 2))
    for arm in ("A", "B", "C"):
        cov = metrics.coverage_by_cycle(cycle_texts, results[arm]["final"])
        results[arm]["coverage"] = cov
        spread = max(cov) - min(cov)
        report.append("| " + arm + " | " +
                      " | ".join(f"{v:.2f}" for v in cov) + f" | {spread:.2f} |")
    report.append("")
    report.append("Höhere Werte = mehr vom jeweiligen Abschnitt im Endprotokoll. "
                  "Ein Abfall nach rechts zeigt Anchoring; eine kleine Spanne zeigt "
                  "gleichmäßige Gewichtung.")
    report.append("")

    report.append("## Kosten und Laufzeit")
    report.append("")
    report.append("| Arm | Prompt-Tokens gesamt | letzter Zyklus | Ausgabe-Tokens | Latenz Median |")
    report.append("|---|---|---|---|---|")
    for arm in ("A", "B", "C"):
        pc = results[arm]["per_cycle"]
        if not pc:
            continue
        ptot = sum(x["prompt_tokens"] or 0 for x in pc)
        plast = pc[-1]["prompt_tokens"]
        ctot = sum(x["completion_tokens"] or 0 for x in pc)
        lat = statistics.median(x["latency_s"] for x in pc)
        report.append(f"| {arm} | {ptot} | {plast} | {ctot} | {lat:.1f}s |")
    report.append("")
    if results["C"].get("invalid_ops"):
        report.append(f"Arm C: {results['C']['invalid_ops']} ungültige Operationen.")
        report.append("")

    (OUT / "report.md").write_text("\n".join(report), encoding="utf-8")
    (OUT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--transcript", default=str(OUT / "excerpt-5min.json"))
    p.add_argument("--model", default="qwen3.5:9b")
    p.add_argument("--base-url", default="http://localhost:11434")
    p.add_argument("--api-key", default=None)
    p.add_argument("--window", type=float, default=60.0)
    run(p.parse_args())
