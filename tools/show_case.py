#!/usr/bin/env python3
"""Show one case from a published run: the prompt, the expected answer, and every model's output.

  python3 tools/show_case.py docs/results/pi5-off-official json-24h-times
  python3 tools/show_case.py docs/results/pi5-off-official agent-no-op-state --round 2
  python3 tools/show_case.py docs/results/pi5-on-gemma4-e2b context-long-revision --thinking

Standard library only, like the runner.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from light_eval import CURRENT_SUITE, suite_module, suite_of  # noqa: E402
from build_site import expected  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path, help="a run folder, e.g. docs/results/pi5-off-official")
    parser.add_argument("case", help="case id, e.g. json-24h-times")
    parser.add_argument("--round", type=int, default=1, help="which pass to show (default: 1)")
    parser.add_argument("--thinking", action="store_true", help="also print each model's reasoning, if any")
    args = parser.parse_args()

    manifest = json.loads((args.run / "manifest.json").read_text())
    # Cases come from the suite the run used; a run of an unknown version (a pilot) falls back to the current one.
    suite = suite_of(manifest) or CURRENT_SUITE
    module = suite_module(suite)
    if manifest.get("suite_version") != module.VERSION:
        print(f"note: the run used suite {manifest.get('suite_version')}; the prompt below is from suite "
              f"{module.VERSION}\n")
    case = {item.id: item for item in module.dataset()}.get(args.case)
    if case is None:
        sys.exit(f"unknown case {args.case!r}")
    print(f"{case.id} ({case.track}, {case.difficulty})" + (f", image fixtures/{case.image}" if case.image else ""))
    print(f"\nPrompt:\n{case.prompt}\n")
    print(f"Expected: {expected(case)}")
    if getattr(case, "calls", None):
        print(f"Expected tool calls: {[[name, arguments] for name, arguments in case.calls]}")
    if getattr(case, "reference", None) and case.track == "agent":
        print(f"Reference solution: {[[name, arguments] for name, arguments in case.reference]}")
    if getattr(case, "state", None):
        print(f"Expected final state: {case.state}")

    rows = [json.loads(line) for line in (args.run / "cases.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["case"] == args.case and row["phase"] == "warm" and row["round"] == args.round]
    if not rows:
        sys.exit(f"no warm results for {args.case!r} in round {args.round}")
    for row in sorted(rows, key=lambda item: item["model"]):
        print(f"\n--- {row['model']}: {row['status']}")
        if row.get("tool_trace"):
            print("tool calls:", [[call.get("name"), call.get("arguments")] for call in row["tool_trace"]])
        thinking = "\n".join(turn.get("thinking") or "" for turn in row.get("turns") or []).strip()
        if thinking and args.thinking:
            print(f"thinking:\n{thinking}\n")
        elif thinking:
            print(f"(thought for {len(thinking.split())} words; add --thinking to see it)")
        content = (row.get("turns") or [{}])[-1].get("content") or row.get("reason") or ""
        print(content.strip())


if __name__ == "__main__":
    main()
