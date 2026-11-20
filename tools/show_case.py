#!/usr/bin/env python3
"""Show one case from a published run: the prompt, the expected answer, and every model's output.

  python3 tools/show_case.py docs/results/pi5-off-official json-24h-times
  python3 tools/show_case.py docs/results/pi5-off-official agent-no-op-state --round 2

Standard library only, like the runner.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cases import VERSION, dataset  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path, help="a run folder, e.g. docs/results/pi5-off-official")
    parser.add_argument("case", help="case id, e.g. json-24h-times")
    parser.add_argument("--round", type=int, default=1, help="which pass to show (default: 1)")
    args = parser.parse_args()

    manifest = json.loads((args.run / "manifest.json").read_text())
    if manifest.get("suite_version") != VERSION:
        print(f"note: the run used suite {manifest.get('suite_version')}; the prompt below is from suite {VERSION}\n")
    case = {item.id: item for item in dataset()}.get(args.case)
    if case is None:
        sys.exit(f"unknown case {args.case!r}")
    print(f"{case.id} ({case.track}, {case.difficulty})" + (f", image fixtures/{case.image}" if case.image else ""))
    print(f"\nPrompt:\n{case.prompt}\n")
    print(f"Expected: {json.dumps(case.answer)}")
    if case.calls:
        print(f"Expected tool calls: {[[name, arguments] for name, arguments in case.calls]}")
    if case.state:
        print(f"Expected final state: {case.state}")

    rows = [json.loads(line) for line in (args.run / "cases.jsonl").read_text().splitlines()]
    rows = [row for row in rows if row["case"] == args.case and row["phase"] == "warm" and row["round"] == args.round]
    if not rows:
        sys.exit(f"no warm results for {args.case!r} in round {args.round}")
    for row in sorted(rows, key=lambda item: item["model"]):
        print(f"\n--- {row['model']}: {row['status']}")
        if row.get("tool_trace"):
            print("tool calls:", [[call.get("name"), call.get("arguments")] for call in row["tool_trace"]])
        content = (row.get("turns") or [{}])[-1].get("content") or row.get("reason") or ""
        print(content.strip())


if __name__ == "__main__":
    main()
