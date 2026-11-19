#!/usr/bin/env python3
"""Publish benchmark runs and rebuild the README results section.

  python3 tools/publish.py add results/<run> [--name NAME]
      Copy a finished run into docs/results/NAME so it can be committed.
      (results/ itself is gitignored.)

  python3 tools/publish.py build
      Rebuild the README section between the results markers, and the
      light/dark charts in docs/assets/, from every published run of the
      current suite version.

Standard library only, like the runner.
"""
import argparse
import json
import math
import shutil
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cases import TRACKS, VERSION, dataset  # noqa: E402
from light_eval import read_rows, summarize, temperature_summary, write_json  # noqa: E402

PUBLISHED = ROOT / "docs" / "results"
ASSETS = ROOT / "docs" / "assets"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
RUN_FILES = ("manifest.json", "cases.jsonl", "blocks.jsonl")
TRACK_NAMES = {"arithmetic": "Arithmetic", "structured": "Structured", "agent": "Agent",
               "vision": "Vision", "context": "Context"}

# Chart tokens: validated two-slot categorical palette (thinking off, thinking on)
# plus chrome, for each colour scheme.
# Round values for the log-scale time axis, in seconds.
TIME_TICKS = (0.1, 0.3, 1, 3, 10, 30, 60, 180, 600, 1800)
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "off": "#2a78d6", "on": "#eb6834"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "off": "#3987e5", "on": "#d95926"},
}


def add(run, name):
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("status") != "complete":
        sys.exit(f"{run} is not a complete run (status {manifest.get('status')!r})")
    target = PUBLISHED / (name or run.name)
    if target.exists():
        sys.exit(f"{target} already exists")
    target.mkdir(parents=True)
    for file in RUN_FILES:
        shutil.copy2(run / file, target / file)
    write_json(target / "summary.json", summarize(read_rows(target / "cases.jsonl"),
                                                  read_rows(target / "blocks.jsonl")))
    print(f"Published {run} as {target.relative_to(ROOT)}; now run: python3 tools/publish.py build")


def device(manifest):
    model = manifest.get("device_tree_model") or ""
    if model.startswith("Raspberry Pi 5"):
        return "Raspberry Pi 5 (8 GB)"
    if "macOS" in (manifest.get("platform") or ""):
        accelerated = manifest.get("inference_options", {}).get("device") != "cpu"
        return "Mac, Apple silicon" + (" (Metal)" if accelerated else " (CPU)")
    return manifest.get("platform") or "unknown device"


def entries():
    """One entry per published run and model, for the current suite version."""
    found = []
    for directory in sorted(PUBLISHED.iterdir() if PUBLISHED.exists() else []):
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest.get("suite_version") != VERSION or manifest.get("status") != "complete":
            continue
        rows = [row for row in read_rows(directory / "cases.jsonl") if row["phase"] == "warm"]
        blocks = [block for block in read_rows(directory / "blocks.jsonl") if block["phase"] == "warm"]
        temperatures = {item["model"]: item for item in temperature_summary(rows)}
        for model in sorted({row["model"] for row in rows}):
            mine = [row for row in rows if row["model"] == model and row["status"] != "unsupported"]
            rounds = len({row["round"] for row in mine}) or 1
            envs = [block["environment"] for block in blocks if block["model"] == model]
            found.append({
                "run": directory.name, "device": device(manifest), "model": model,
                "thinking": manifest["options"]["thinking"], "cycles": manifest["options"]["cycles"],
                "rounds": rounds,
                "score": sum(row["status"] == "pass" for row in mine) / rounds,
                "challenge": sum(row["status"] == "pass" and row["difficulty"] == "challenge"
                                 for row in mine) / rounds,
                "tracks": {track: sum(row["status"] == "pass" and row["track"] == track for row in mine) / rounds
                           for track in TRACKS},
                "unsupported": sum(row["status"] == "unsupported" for row in rows if row["model"] == model),
                "timeouts": sum(row["status"] == "timeout" for row in mine) / rounds,
                "median_s": statistics.median(row["wall_s"] for row in mine if row.get("wall_s") is not None),
                "min_available_kib": min((env["min_mem_available_kib"] for env in envs
                                          if env.get("min_mem_available_kib") is not None), default=None),
                "peak_swap_kib": max((env["peak_swap_used_kib"] for env in envs
                                      if env.get("peak_swap_used_kib") is not None), default=None),
                "oom": any(row["status"] == "out_of_memory" for row in mine)
                       or any(env.get("system_oom_kills") for env in envs),
                "temperature": temperatures.get(model),
            })
    return found


def number(value):
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def memory(entry):
    if entry["oom"]:
        return "❌ out of memory"
    available = entry["min_available_kib"]
    if available is None or not entry["device"].startswith("Raspberry"):
        return "—"
    free = (f"{available / 2**20:.1f} GB free" if available >= 2**20 else f"{available / 1024:.0f} MB free")
    swap = entry["peak_swap_kib"] or 0
    if available < 2**20 // 2 or swap > 2**20:
        return f"⚠️ tight: {free}, {swap / 2**20:.1f} GB swap"
    return f"✅ {free}"


def table(group, totals):
    head = ["Model", f"Score /{totals['all']}", f"Challenge /{totals['challenge']}",
            *(f"{TRACK_NAMES[track]} /{totals[track]}" for track in TRACKS),
            "Median time per case", "Timed out", "Memory (lowest free)", "Peak CPU"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] + ["---:"] * (len(head) - 3) + ["---", "---"]) + "|"]
    for entry in sorted(group, key=lambda item: (-item["score"], item["median_s"])):
        temperature = entry["temperature"]
        peak = "—"
        if temperature:
            peak = f"{temperature['peak_cpu_c']:.0f} °C"
            if temperature["throttled_cases"]:
                peak += f" ⚠️ {temperature['throttled_cases']} throttled"
        lines.append("| " + " | ".join([
            f"`{entry['model']}`", f"**{number(entry['score'])}**", number(entry["challenge"]),
            *(number(entry["tracks"][track]) for track in TRACKS),
            duration(entry["median_s"]), number(entry["timeouts"]), memory(entry), peak]) + " |")
    return "\n".join(lines)


def svg_chart(points, theme):
    """Score against median time per case (log scale), one colour per thinking mode."""
    t = THEMES[theme]
    width, height = 720, 400
    left, right, top, bottom = 64, 40, 48, 56
    plot_w, plot_h = width - left - right, height - top - bottom
    total = len(dataset())
    times = [point["median_s"] for point in points]
    ticks = [tick for tick in TIME_TICKS if tick <= max(times)]
    ticks = [tick for tick in ticks if tick >= max(t for t in TIME_TICKS if t <= min(times))]
    ticks.append(min(t for t in TIME_TICKS if t > max(times)))
    low, high = ticks[0], ticks[-1]

    def x(seconds):
        return left + plot_w * (math.log10(seconds) - math.log10(low)) / (math.log10(high) - math.log10(low))

    def y(score):
        return top + plot_h * (1 - score / total)

    font = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-label="Score against median time per case">',
           f'<rect width="{width}" height="{height}" rx="8" fill="{t["surface"]}"/>']
    for score in range(0, total + 1, 10):
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y(score):.1f}" y2="{y(score):.1f}" '
                   f'stroke="{t["grid"] if score else t["axis"]}" stroke-width="1"/>')
        out.append(f'<text x="{left - 10}" y="{y(score) + 4:.1f}" text-anchor="end" font-size="12" '
                   f'fill="{t["muted"]}" {font}>{score}</text>')
    for tick in ticks:
        out.append(f'<line x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{top}" y2="{top + plot_h}" '
                   f'stroke="{t["grid"]}" stroke-width="1"/>')
        label = f"{tick:g} s" if tick < 60 else f"{tick / 60:g} min"
        out.append(f'<text x="{x(tick):.1f}" y="{top + plot_h + 20}" text-anchor="middle" font-size="12" '
                   f'fill="{t["muted"]}" {font}>{label}</text>')
    out.append(f'<text x="{left + plot_w / 2}" y="{height - 12}" text-anchor="middle" font-size="12" '
               f'fill="{t["ink2"]}" {font}>Median time per case (log scale)</text>')
    out.append(f'<text x="{left}" y="{top - 20}" font-size="12" fill="{t["ink2"]}" {font}>'
               f'Cases passed (of {total})</text>')
    # Legend: always present for two series; identity never relies on colour alone
    # because every point is also labelled with its model.
    modes = [mode for mode in ("off", "on") if any(point["thinking"] == mode for point in points)]
    for index, mode in enumerate(modes):
        label = f"Thinking {mode}"
        lx = left + plot_w - 115 * len(modes) + 10 + index * 115
        out.append(f'<circle cx="{lx}" cy="{top - 24}" r="5" fill="{t[mode]}"/>')
        out.append(f'<text x="{lx + 10}" y="{top - 20}" font-size="12" fill="{t["ink2"]}" {font}>{label}</text>')
    for point in sorted(points, key=lambda item: item["score"]):
        px, py = x(point["median_s"]), y(point["score"])
        color = t["on" if point["thinking"] == "on" else "off"]
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{color}" stroke="{t["surface"]}" '
                   f'stroke-width="2"/>')
        # Labels near the right edge go on the left of the point so they stay inside the chart.
        flip = px > left + plot_w - 160
        out.append(f'<text x="{px - 10 if flip else px + 10:.1f}" y="{py + 4:.1f}" font-size="12" '
                   f'text-anchor="{"end" if flip else "start"}" fill="{t["ink"]}" {font}>'
                   f'{point["model"]} ({number(point["score"])})</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-").replace("--", "-")


def build():
    found = entries()
    cases = dataset()
    totals = {"all": len(cases), "challenge": sum(case.difficulty == "challenge" for case in cases),
              **{track: sum(case.track == track for case in cases) for track in TRACKS}}
    parts = [START, "",
             f"_Generated by `python3 tools/publish.py build` from the runs in "
             f"[`docs/results/`](docs/results/). Suite {VERSION}: {totals['all']} cases "
             f"({totals['all'] - totals['challenge']} control, {totals['challenge']} challenge)._", ""]
    if not found:
        parts += ["No published runs for this suite version yet.", ""]
    by_device = defaultdict(list)
    for entry in found:
        by_device[entry["device"]].append(entry)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name in sorted(by_device, key=lambda item: (not item.startswith("Raspberry"), item)):
        group = by_device[name]
        parts += [f"### {name}", ""]
        if any(entry["cycles"] < 2 for entry in group):
            parts += ["> Exploratory: one cycle per model. Official runs will use `--cycles 2`.", ""]
        for mode in ("off", "on"):
            subset = [entry for entry in group if entry["thinking"] == mode]
            if subset:
                parts += [f"**Thinking {mode}**", "", table(subset, totals), ""]
        base = f"score-vs-time-{slug(name)}"
        for theme in THEMES:
            (ASSETS / f"{base}-{theme}.svg").write_text(svg_chart(group, theme))
        parts += ["<picture>",
                  f'  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/{base}-dark.svg">',
                  f'  <img alt="Cases passed against median time per case on {name}, thinking off and on" '
                  f'src="docs/assets/{base}-light.svg" width="720">',
                  "</picture>", ""]
    parts.append(END)
    text = README.read_text()
    if START not in text or END not in text:
        sys.exit(f"README.md needs the markers {START} and {END}")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    README.write_text(before + "\n".join(parts) + after)
    print(f"Rebuilt README results from {len(found)} model runs")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    add_command = commands.add_parser("add", help="copy a finished run into docs/results/")
    add_command.add_argument("run", type=Path)
    add_command.add_argument("--name", help="published directory name (default: the run's name)")
    commands.add_parser("build", help="rebuild the README results section and charts")
    args = parser.parse_args()
    if args.command == "add":
        add(args.run, args.name)
    else:
        build()


if __name__ == "__main__":
    main()
