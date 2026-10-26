"""Prüft, ob die Unterteilungsschwäche von Arm C am Prompt liegt."""
from __future__ import annotations
import json, sys
from pathlib import Path
import arms, metrics
from run_comparison import load_cycles

VARIANT = arms.SYSTEM_C.replace(
    "- Ein neues Thema nur, wenn es wirklich ein anderes Thema ist; sonst\n"
    "  extend_topic auf das passende vorhandene.",
    "- Lieber ein Thema zu viel als eines zu wenig: Sobald ein neuer Aspekt\n"
    "  eigenständig genug ist, um im Protokoll eine eigene Überschrift zu\n"
    "  verdienen, legst du ihn als neues Thema an. extend_topic nur, wenn es\n"
    "  wirklich derselbe Punkt ist.\n"
    "- Ein Thema deckt einen Aspekt ab, nicht ein ganzes Gespräch. Bei einer\n"
    "  breiten Diskussion sind fünf bis zehn Themen normal.")

def main() -> None:
    cycles = load_cycles(Path("out/full.json"), 90.0)
    kw = dict(model="qwen3.5:9b", base_url="http://localhost:11434", api_key=None)
    state = arms.ProtocolState()
    total_prompt = total_out = 0
    for c in cycles:
        user = arms.USER_C.format(digest=state.digest(), segment=c["text"])
        from llm import chat, extract_json
        content, usage, latency = chat(system=VARIANT, user=user, json_mode=True, **kw)
        total_prompt += usage.get("prompt_tokens", 0)
        total_out += usage.get("completion_tokens", 0)
        try:
            ops = extract_json(content).get("ops", [])
        except Exception:
            ops = []
        state.apply(ops, c["index"])
        print(f"  Zyklus {c['index']}: {latency:4.1f}s  ops={len(ops):>2}  Themen={len(state.topics):>2}")
    final = state.render()
    Path("out/armC2-final.md").write_text(final, encoding="utf-8")
    print(f"\nThemen: {len(state.topics)} | Zeichen: {len(final)} | "
          f"Prompt-Tokens: {total_prompt} | Ausgabe: {total_out}")
    print("Überschriften:")
    for t in state.topics:
        print(f"  - {t.title}")

main()
