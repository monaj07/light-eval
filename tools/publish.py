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


def parse_rounds(text):
    """'1-3' or '1,2,3' -> [1, 2, 3]."""
    rounds = set()
    for part in text.split(","):
        low, _, high = part.partition("-")
        rounds.update(range(int(low), int(high or low) + 1))
    return sorted(rounds)


def add(run, name, rounds=None, note=None, flags=()):
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("status") != "complete" and not rounds:
        sys.exit(f"{run} is not a complete run (status {manifest.get('status')!r}). To publish only its "
                 "complete rounds, pass --rounds and a --note saying why the rest are left out.")
    if rounds and not note:
        sys.exit("--rounds needs a --note saying why the other rounds are left out")
    target = PUBLISHED / (name or run.name)
    if target.exists():
        sys.exit(f"{target} already exists")
    target.mkdir(parents=True)
    keep = set(parse_rounds(rounds)) if rounds else None
    for file in ("cases.jsonl", "blocks.jsonl"):
        lines = (run / file).read_text().splitlines()
        if keep is not None:
            lines = [line for line in lines if json.loads(line)["round"] in keep]
        (target / file).write_text("".join(line + "\n" for line in lines))
    published = {}
    if keep is not None:
        # The run's own status stays as recorded; this records what was published and why.
        published.update({"rounds": sorted(keep), "note": note, "original_status": manifest.get("status")})
    if flags:
        published["flags"] = dict(flag.split("=", 1) for flag in flags)
    if published:
        manifest["published"] = published
    write_json(target / "manifest.json", manifest)
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
        published = manifest.get("published", {})
        if manifest.get("suite_version") != VERSION or (
                manifest.get("status") != "complete" and not published.get("rounds")):
            continue
        all_rows = read_rows(directory / "cases.jsonl")
        rows = [row for row in all_rows if row["phase"] == "warm"]
        blocks = [block for block in read_rows(directory / "blocks.jsonl") if block["phase"] == "warm"]
        temperatures = combined_temperatures(temperature_summary(rows))
        for model in sorted({row["model"] for row in rows}):
            mine = [row for row in rows if row["model"] == model and row["status"] != "unsupported"]
            rounds = len({row["round"] for row in mine}) or 1
            envs = [block["environment"] for block in blocks if block["model"] == model]
            found.append({
                "run": directory.name, "device": device(manifest), "model": model,
                "thinking": manifest["options"]["thinking"], "cycles": manifest["options"]["cycles"],
                # Official runs record the hardware setup (--setup); everything else is exploratory.
                "setup": (manifest["options"].get("setup") or "").strip(),
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
                # Peak memory in use, system-wide: RAM in use plus zram swap, per warm phase.
                "peak_mem_gb": max((((env.get("peak_system_used_estimate_kib") or 0)
                                     + (env.get("peak_swap_used_kib") or 0)) / 2**20
                                    for env in envs if env.get("peak_system_used_estimate_kib")), default=None),
                "flag": published.get("flags", {}).get(model),
                "note": published.get("note"),
                **speed(all_rows, model),
            })
    return found


def decode_rate(turn):
    metrics = turn.get("metrics") or {}
    if metrics.get("eval_count") and metrics.get("eval_duration"):
        return metrics["eval_count"] / (metrics["eval_duration"] / 1e9)
    return None


def speed(rows, model):
    """Cold load time and sustained generation speed, where the run measured them."""
    cold = sorted((row for row in rows if row["model"] == model and row["phase"] == "cold"
                   and row.get("turns") and (row["turns"][0].get("metrics") or {}).get("load_duration")),
                  key=lambda row: row["round"])
    # Only the first load after the reboot reads the model from storage; later loads
    # can come from the operating system's file cache.
    first_load_s = cold[0]["turns"][0]["metrics"]["load_duration"] / 1e9 if cold else None
    rates, changes = [], []
    for round_num in sorted({row["round"] for row in rows if row["model"] == model}):
        sustained = [decode_rate(row["turns"][0]) for row in rows
                     if row["model"] == model and row["round"] == round_num and row["phase"] == "sustained"
                     and row["status"] == "complete" and row.get("turns")]
        sustained = [rate for rate in sustained if rate]
        rates += sustained
        if len(sustained) >= 3:
            third = len(sustained) // 3
            first, last = statistics.mean(sustained[:third]), statistics.mean(sustained[-third:])
            changes.append(last / first - 1)
    return {"first_load_s": first_load_s,
            "sustained_tok_s": statistics.median(rates) if rates else None,
            "sustained_change": statistics.mean(changes) if changes else None}


def combined_temperatures(items):
    """One temperature summary per model across all its rounds (temperature_summary is per round)."""
    by_model = defaultdict(list)
    for item in items:
        by_model[item["model"]].append(item)
    combined = {}
    for model, rounds in by_model.items():
        weighted = [(item["mean_cpu_c"], item["cases"]) for item in rounds if item["mean_cpu_c"] is not None]
        combined[model] = {
            "mean_cpu_c": (sum(mean * n for mean, n in weighted) / sum(n for _, n in weighted)
                           if weighted else None),
            "peak_cpu_c": max(item["peak_cpu_c"] for item in rounds),
            "throttled_cases": sum(item["throttled_cases"] for item in rounds),
        }
    return combined


def number(value):
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def memory(entry):
    if entry["flag"]:
        return f"❌ {entry['flag']}"
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
            "Median time per case", "Timed out", "Memory (lowest free)", "CPU °C (mean / peak)"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] + ["---:"] * (len(head) - 3) + ["---", "---"]) + "|"]
    for entry in sorted(group, key=lambda item: (-item["score"], item["median_s"])):
        temperature = entry["temperature"]
        peak = "—"
        if temperature:
            mean = temperature.get("mean_cpu_c")
            peak = f"{mean:.0f} / {temperature['peak_cpu_c']:.0f}" if mean is not None else \
                f"— / {temperature['peak_cpu_c']:.0f}"
            if temperature["throttled_cases"]:
                peak += f" ⚠️ {temperature['throttled_cases']} throttled"
        lines.append("| " + " | ".join([
            f"`{entry['model']}`" + ("" if entry["setup"] else " †"), f"**{number(entry['score'])}**", number(entry["challenge"]),
            *(number(entry["tracks"][track]) for track in TRACKS),
            duration(entry["median_s"]), number(entry["timeouts"]), memory(entry), peak]) + " |")
    return "\n".join(lines)


def speed_table(group):
    lines = ["| Model | First load from storage | Generation speed (sustained) | Speed change during the sustained phase |",
             "|---|---:|---:|---:|"]
    for entry in sorted(group, key=lambda item: item["model"]):
        load = f"{entry['first_load_s']:.0f} s" if entry["first_load_s"] is not None else "—"
        rate = f"{entry['sustained_tok_s']:.1f} tokens/s" if entry["sustained_tok_s"] else "—"
        change = f"{entry['sustained_change'] * 100:+.1f}%" if entry["sustained_change"] is not None else "—"
        lines.append(f"| `{entry['model']}` | {load} | {rate} | {change} |")
    return "\n".join(lines)


def svg_chart(points, theme, title):
    """Accuracy against speed. Bubble area shows peak memory in use, colour shows the thinking
    mode, and a thin connector joins each model's thinking-off and thinking-on points."""
    t = THEMES[theme]
    width, height = 920, 470
    left, top, plot_w, plot_h = 64, 92, 560, 300
    legend_x = left + plot_w + 36
    total = len(dataset())
    font = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
    with_memory = [point for point in points if point["peak_mem_gb"]]
    # Area encodes memory: radius grows with the square root, 10 GB -> 26 px.
    scale = 26 / math.sqrt(10)

    def radius(point):
        return scale * math.sqrt(point["peak_mem_gb"]) if point["peak_mem_gb"] else 6

    # Pad the log-scale time axis so the largest bubbles stay inside the plot.
    times = [point["median_s"] for point in points]
    low = max(tick for tick in TIME_TICKS if tick <= min(times) / 1.4) if min(times) / 1.4 >= TIME_TICKS[0] \
        else TIME_TICKS[0]
    high = min((tick for tick in TIME_TICKS if tick >= max(times) * 1.4), default=TIME_TICKS[-1])
    ticks = [tick for tick in TIME_TICKS if low <= tick <= high]

    def x(seconds):
        return left + plot_w * (math.log10(seconds) - math.log10(low)) / (math.log10(high) - math.log10(low))

    def y(score):
        return top + plot_h * (1 - score / total)

    def text(px, py, content, size=12, fill="ink2", anchor="start", weight="normal", halo=False):
        # A halo in the surface colour keeps labels legible where they cross a connector.
        outline = f'stroke="{t["surface"]}" stroke-width="4" paint-order="stroke" ' if halo else ""
        return (f'<text x="{px:.1f}" y="{py:.1f}" font-size="{size}" font-weight="{weight}" '
                f'text-anchor="{anchor}" fill="{t[fill]}" {outline}{font}>{content}</text>')

    subtitle = "Higher is more accurate, further left is faster. Colour: thinking mode"
    subtitle += "; bubble area: peak memory in use (RAM + zram)." if with_memory else "."
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
           f'<rect width="{width}" height="{height}" rx="8" fill="{t["surface"]}"/>',
           text(left - 40, 34, title, size=16, fill="ink", weight="600"),
           text(left - 40, 56, subtitle, size=12, fill="ink2")]
    for score in range(0, total + 1, 10):
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y(score):.1f}" y2="{y(score):.1f}" '
                   f'stroke="{t["grid"] if score else t["axis"]}" stroke-width="1"/>')
        out.append(text(left - 10, y(score) + 4, score, fill="muted", anchor="end"))
    for tick in ticks:
        out.append(f'<line x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{top}" y2="{top + plot_h}" '
                   f'stroke="{t["grid"]}" stroke-width="1"/>')
        label = f"{tick:g} s" if tick < 60 else f"{tick / 60:g} min"
        out.append(text(x(tick), top + plot_h + 20, label, fill="muted", anchor="middle"))
    out.append(text(left + plot_w / 2, top + plot_h + 44, "Median time per case (log scale)", anchor="middle"))
    out.append(text(left, top - 12, f"Cases passed (of {total})"))
    out.append(text(left + plot_w - 6, top + 16, "better ↖", fill="muted", anchor="end"))

    # Connectors first, so the bubbles sit on top of them.
    by_model = defaultdict(dict)
    for point in points:
        by_model[point["model"]][point["thinking"]] = point
    for modes in by_model.values():
        if "off" in modes and "on" in modes:
            a, b = modes["off"], modes["on"]
            out.append(f'<line x1="{x(a["median_s"]):.1f}" y1="{y(a["score"]):.1f}" x2="{x(b["median_s"]):.1f}" '
                       f'y2="{y(b["score"]):.1f}" stroke="{t["muted"]}" stroke-width="1.5" stroke-dasharray="1 4" '
                       f'stroke-linecap="round"/>')
    # Largest bubbles first, so small ones stay visible on top.
    bubbles = []
    for point in sorted(points, key=lambda item: -radius(item)):
        px, py, r = x(point["median_s"]), y(point["score"]), radius(point)
        bubbles.append((point, px, py, r))
        color = t["on" if point["thinking"] == "on" else "off"]
        dashed = ' stroke-dasharray="4 3"' if point["flag"] else ""
        ring = t["ink2"] if point["flag"] else t["surface"]
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="{color}" fill-opacity="0.78" '
                   f'stroke="{ring}" stroke-width="2"{dashed}/>')

    def hits_circle(box, cx, cy, r):
        x0, y0, x1, y1 = box
        nearest_x, nearest_y = min(max(cx, x0), x1), min(max(cy, y0), y1)
        return (nearest_x - cx) ** 2 + (nearest_y - cy) ** 2 < r * r

    # Each label tries right, left, above and below its bubble, taking the first spot that
    # stays inside the plot and clear of every bubble and earlier label.
    placed = []
    for point, px, py, r in sorted(bubbles, key=lambda item: item[2]):
        label = f'{point["model"]} · {number(point["score"])}'
        if point["flag"]:
            label += " (hung the Pi)"
        w, h = 6.6 * len(label), 14
        candidates = [(px + r + 8, py + 4, "start"), (px - r - 8, py + 4, "end"),
                      (px, py - r - 8, "middle"), (px, py + r + 18, "middle")]

        def box(cand):
            lx, ly, anchor = cand
            x0 = lx if anchor == "start" else lx - w if anchor == "end" else lx - w / 2
            return (x0, ly - h + 3, x0 + w, ly + 3)

        def clear(cand):
            x0, y0, x1, y1 = box(cand)
            inside = left <= x0 and x1 <= left + plot_w + 30 and top <= y0 and y1 <= top + plot_h
            return inside and not any(hits_circle((x0, y0, x1, y1), cx, cy, cr)
                                      for other, cx, cy, cr in bubbles if other is not point) \
                and not any(x0 < b[2] and b[0] < x1 and y0 < b[3] and b[1] < y1 for b in placed)
        choice = next((cand for cand in candidates if clear(cand)), candidates[0])
        placed.append(box(choice))
        out.append(text(choice[0], choice[1], label, fill="ink", anchor=choice[2], halo=True))

    # Legend column: thinking modes, then memory sizes, then the flag marker.
    ly = top + 6
    modes = [mode for mode in ("off", "on") if any(point["thinking"] == mode for point in points)]
    out.append(text(legend_x, ly, "Thinking", fill="ink", weight="600"))
    for mode in modes:
        ly += 22
        out.append(f'<circle cx="{legend_x + 7}" cy="{ly - 4}" r="6" fill="{t[mode]}" fill-opacity="0.78"/>')
        out.append(text(legend_x + 20, ly, mode))
    if with_memory:
        ly += 34
        out.append(text(legend_x, ly, "Memory in use", fill="ink", weight="600"))
        for gb in (2, 4, 8):
            r = scale * math.sqrt(gb)
            ly += 2 * r + 6
            out.append(f'<circle cx="{legend_x + 22}" cy="{ly - r:.1f}" r="{r:.1f}" fill="none" '
                       f'stroke="{t["muted"]}" stroke-width="1.5"/>')
            out.append(text(legend_x + 54, ly - r + 4, f"{gb} GB"))
        for offset, line in enumerate(("For scale: the Pi has", "8 GB of RAM and", "2 GB of zram.")):
            out.append(text(legend_x, ly + 22 + 15 * offset, line, size=11, fill="muted"))
        ly += 52
    if any(point["flag"] for point in points):
        ly += 34
        out.append(f'<circle cx="{legend_x + 9}" cy="{ly - 4}" r="8" fill="none" stroke="{t["ink2"]}" '
                   f'stroke-width="2" stroke-dasharray="4 3"/>')
        out.append(text(legend_x + 24, ly, "Didn't reliably fit"))
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
        # An official run replaces exploratory runs of the same model and thinking mode.
        official = {(entry["model"], entry["thinking"]) for entry in by_device[name] if entry["setup"]}
        group = [entry for entry in by_device[name]
                 if entry["setup"] or (entry["model"], entry["thinking"]) not in official]
        parts += [f"### {name}", ""]
        setups = sorted({entry["setup"] for entry in group if entry["setup"]})
        for setup in setups:
            parts += [f"Setup: {setup}", ""]
        for note in sorted({entry["note"] for entry in group if entry["note"]}):
            parts += [f"Note: {note}", ""]
        if any(not entry["setup"] for entry in group):
            parts += ["† Exploratory run: one pass, no recorded hardware setup. "
                      "[Official runs](docs/OFFICIAL_RUNS.md) will replace these rows.", ""]
        for mode in ("off", "on"):
            subset = [entry for entry in group if entry["thinking"] == mode]
            if subset:
                rounds = sorted({entry["rounds"] for entry in subset})
                label = f"**Thinking {mode}**"
                if rounds != [1]:
                    label += f" (scores averaged over {'/'.join(map(str, rounds))} passes)"
                parts += [label, "", table(subset, totals), ""]
        timed = [entry for entry in group if entry["first_load_s"] is not None or entry["sustained_tok_s"]]
        if timed:
            parts += ["**Load time and generation speed**", "", speed_table(timed), ""]
        base = f"score-vs-time-{slug(name)}"
        for theme in THEMES:
            (ASSETS / f"{base}-{theme}.svg").write_text(svg_chart(group, theme, f"Accuracy and speed on {name}"))
        parts += ["<picture>",
                  f'  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/{base}-dark.svg">',
                  f'  <img alt="Cases passed against median time per case on {name}; colour shows the thinking mode and bubble area the peak memory in use" '
                  f'src="docs/assets/{base}-light.svg" width="920">',
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
    add_command.add_argument("--rounds", help="publish only these complete rounds, e.g. 1-3 (needs --note)")
    add_command.add_argument("--note", help="why some rounds are left out; shown with the results")
    add_command.add_argument("--flag", action="append", default=[], metavar="MODEL=TEXT",
                             help="a warning shown in that model's memory column, e.g. a hang")
    commands.add_parser("build", help="rebuild the README results section and charts")
    args = parser.parse_args()
    if args.command == "add":
        add(args.run, args.name, args.rounds, args.note, args.flag)
    else:
        build()


if __name__ == "__main__":
    main()
