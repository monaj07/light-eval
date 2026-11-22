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
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cases import TRACKS, VERSION, dataset  # noqa: E402
from light_eval import (INFRASTRUCTURE_STATUSES, apply_budget_defaults, build_parser, read_rows,  # noqa: E402
                        summarize, temperature_summary, write_json)
import charts  # noqa: E402  (tools/charts.py)

PUBLISHED = ROOT / "docs" / "results"
ASSETS = ROOT / "docs" / "assets"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
TRACK_NAMES = {"arithmetic": "Arithmetic", "structured": "Structured", "agent": "Agent",
               "vision": "Vision", "context": "Context"}

# Chart tokens: validated two-slot categorical palette (thinking off, thinking on)
# plus chrome, for each colour scheme.
# The Pi used for the published results, shown under its setup line.
HARDWARE_PHOTOS = (
    ("docs/assets/hardware/raspberry-pi-hardware.jpg",
     "Raspberry Pi 5 8 GB, Active Cooler, acrylic case, SanDisk Extreme 128 GB microSD card and 27 W power supply"),
    ("docs/assets/hardware/raspberry-pi-assembled.jpg", "The assembled Raspberry Pi 5 in its vented acrylic case"),
)
# Thread count used by the main results; runs with other thread counts feed the comparison table.
PRIMARY_THREADS = 4
# Tracks every model can attempt; vision needs the vision capability.
TEXT_TRACKS = ("arithmetic", "structured", "agent", "context")
THREADS_NOTE = (
    "Each pair compares an official run at the main thread count with one at the other, for the same model and "
    "thinking mode; everything else is identical. Each cell reads from {low} threads to {high} (\"19.5 → 11.8 s\"), with the "
    "relative change where it helps: 🟢 marks an improvement (faster, more accurate, fewer timeouts, cooler) and 🔴 "
    "a change for the worse. Prompt "
    "processing is limited by computation, so more cores can speed it up. Generation is limited mostly by memory "
    "bandwidth, which the cores share, so more threads may not help it and can even slow it down. With thinking "
    "on, a change in speed also changes how many cases finish inside the {limit} limit."
)
SPEED_NOTE = (
    "Prompt processing is how fast a model reads its input, and it is limited by computation; generation is how "
    "fast it writes, and it is limited mostly by memory bandwidth. These runs use all 4 of the Pi's cores "
    "(`--threads 4`). \"In cases\" is generation during the 65 cases, "
    "whose answers are short JSON; \"sustained\" is 90 seconds of continuous free prose. The Gemma builds include a "
    "small draft model for speculative decoding, which likely explains why they generate faster on short, "
    "predictable JSON than on prose."
)
TIME_NOTE = (
    "Answers are short, so most of each case is spent reading the prompt, especially for vision and long-context "
    "cases. That is why a model can generate quickly and still take the longest per case. Times are medians over "
    "cases, from Ollama's own prompt and generation durations, so reading and writing don't add up exactly to the "
    "median time per case."
)
MEMORY_NOTE = (
    "Peak memory in use is system-wide (Raspberry Pi OS and Ollama included): RAM in use plus pages "
    "swapped to zram, at the warm phase's peak. The Pi has 7.9 GB of usable RAM and 2 GB of zram. ≈ marks runs "
    "recorded before zram's own RAM use was measured: they add the separate peaks of RAM and swap, and count "
    "zram's compressed pages twice, so they overstate memory in use when zram is busy. ✅ more than 0.5 GB "
    "stayed free and swap stayed under 1 GB; ⚠️ less free or more swap; ❌ ran out of memory or hung the Pi."
)
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


# Options that change what a score or a time means. Threads may differ (that is the thread
# comparison); everything else must match the runner's defaults for the thinking mode.
PROTOCOL_OPTIONS = ("context", "max_tokens", "timeout", "tool_rounds", "tool_calls")


def standard_options(thinking):
    args = build_parser().parse_args(["run", "--models", "model", "--thinking", thinking])
    return {key: getattr(args, key) for key in PROTOCOL_OPTIONS if hasattr(args, key)} | {
        key: getattr(apply_budget_defaults(args), key) for key in ("context", "max_tokens")}


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_flags(flags, models):
    parsed = {}
    for flag in flags:
        model, sep, text = flag.partition("=")
        if not sep or not model or not text:
            sys.exit(f"--flag needs MODEL=TEXT, got {flag!r}")
        if model not in models:
            sys.exit(f"--flag names {model!r}, which isn't in this run (models: {', '.join(sorted(models))})")
        parsed[model] = text
    return parsed


def run_problems(manifest, rows, keep=None, allow_errors=False):
    """Everything that would make a run's results mean something different from the published ones.

    Returns a list of problems; an empty list means the run can be published as it is."""
    problems = []
    options = manifest.get("options", {})
    if manifest.get("suite_version") != VERSION:
        problems.append(f"suite {manifest.get('suite_version')}, but the current suite is {VERSION}")
    if list(options.get("tracks") or []) != list(TRACKS):
        problems.append(f"ran only some tracks ({', '.join(options.get('tracks') or [])}); a published run "
                        "needs every track, or a missing track reads as a missing capability")
    if options.get("thinking") not in ("off", "on"):
        problems.append(f"thinking {options.get('thinking')!r}; published runs set it explicitly, off or on")
    else:
        for key, value in standard_options(options["thinking"]).items():
            if key in options and options[key] != value:
                problems.append(f"--{key.replace('_', '-')} {options[key]} instead of the standard {value}")
    if str(manifest.get("server_prompt_cache_ram_mib")) != "0":
        problems.append(f"the server's LLAMA_ARG_CACHE_RAM was {manifest.get('server_prompt_cache_ram_mib')!r}, "
                        "not 0, so memory grew with every case")
    hashes = manifest.get("sha256", {})
    for path in [ROOT / "cases.py", *sorted((ROOT / "fixtures").glob("*.png"))]:
        relative = str(path.relative_to(ROOT))
        if hashes.get(relative) != file_hash(path):
            problems.append(f"{relative} differs from the version this run used")
    errors = defaultdict(int)
    for row in rows:
        if row.get("status") in INFRASTRUCTURE_STATUSES:
            errors[f"{row['model']}: {row['status']}"] += 1
    if errors and not allow_errors:
        problems.append("cases failed for infrastructure reasons, which would count against the model: "
                        + ", ".join(f"{key} ×{count}" for key, count in sorted(errors.items()))
                        + ". Rerun, or pass --allow-errors with a --note if they are the model's own failures")
    expected = len(manifest.get("case_metadata") or {})
    warm = defaultdict(int)
    for row in rows:
        if row["phase"] == "warm":
            warm[row["model"], row["round"]] += 1
    for model in manifest.get("models", {}):
        for round_num in sorted(keep or {r for m, r in warm}):
            if warm.get((model, round_num), 0) != expected:
                problems.append(f"round {round_num} of {model} has {warm.get((model, round_num), 0)} of "
                                f"{expected} warm cases")
    return problems


def add(run, name, rounds=None, note=None, flags=(), allow_errors=False):
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("status") not in ("complete", "complete_with_errors") and not rounds:
        sys.exit(f"{run} is not a complete run (status {manifest.get('status')!r}). To publish only its "
                 "complete rounds, pass --rounds and a --note saying why the rest are left out.")
    if (rounds or allow_errors) and not note:
        sys.exit("--rounds and --allow-errors need a --note saying why")
    target = PUBLISHED / (name or run.name)
    if target.exists():
        sys.exit(f"{target} already exists")
    # Check everything before creating anything, so a failed add leaves nothing behind.
    keep = set(parse_rounds(rounds)) if rounds else None
    parsed_flags = parse_flags(flags, set(manifest.get("models", {})))
    files = {}
    for file in ("cases.jsonl", "blocks.jsonl"):
        lines = [line for line in (run / file).read_text().splitlines() if line.strip()]
        rows = read_rows(run / file)  # skips a truncated last line
        lines = lines[:len(rows)]
        if keep is not None:
            lines = [line for row, line in zip(rows, lines) if row["round"] in keep]
        files[file] = lines
    kept_rows = [json.loads(line) for line in files["cases.jsonl"]]
    problems = run_problems(manifest, kept_rows, keep, allow_errors)
    if problems:
        sys.exit(f"{run} can't be published as it is:\n" + "\n".join(f"  - {problem}" for problem in problems))
    target.mkdir(parents=True)
    for file, lines in files.items():
        (target / file).write_text("".join(line + "\n" for line in lines))
    published = {}
    if keep is not None:
        # The run's own status stays as recorded; this records what was published and why.
        published.update({"rounds": sorted(keep), "note": note, "original_status": manifest.get("status")})
    elif note:
        published["note"] = note
    if allow_errors:
        published["errors_accepted"] = True
    if parsed_flags:
        published["flags"] = parsed_flags
    if published:
        manifest["published"] = published
    write_json(target / "manifest.json", manifest)
    write_json(target / "summary.json", summarize(read_rows(target / "cases.jsonl"),
                                                  read_rows(target / "blocks.jsonl")))
    shown = target.relative_to(ROOT) if target.is_relative_to(ROOT) else target
    print(f"Published {run} as {shown}; now run: python3 tools/publish.py build")


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
                manifest.get("status") not in ("complete", "complete_with_errors") and not published.get("rounds")):
            continue
        all_rows = read_rows(directory / "cases.jsonl")
        rows = [row for row in all_rows if row["phase"] == "warm"]
        blocks = [block for block in read_rows(directory / "blocks.jsonl") if block["phase"] == "warm"]
        temperatures = combined_temperatures(temperature_summary(rows))
        for model in sorted({row["model"] for row in rows}):
            mine = [row for row in rows if row["model"] == model and row["status"] != "unsupported"]
            rounds = len({row["round"] for row in mine}) or 1
            times = [row["wall_s"] for row in mine if row.get("wall_s") is not None]
            if not times:
                # Every case failed before timing started (e.g. every warmup ran out of memory).
                print(f"warning: {directory.name}: {model} has no timed cases; left out of the README",
                      file=sys.stderr)
                continue
            capabilities = manifest["models"].get(model, {}).get("capabilities")
            envs = [block["environment"] for block in blocks if block["model"] == model]
            found.append({
                "run": directory.name, "device": device(manifest), "model": model,
                "thinking": manifest["options"]["thinking"], "cycles": manifest["options"]["cycles"],
                # Official runs record the hardware setup (--setup); everything else is exploratory.
                "setup": (manifest["options"].get("setup") or "").strip(),
                "threads": manifest["options"].get("threads"),
                "rounds": rounds,
                "score": sum(row["status"] == "pass" for row in mine) / rounds,
                "challenge": sum(row["status"] == "pass" and row["difficulty"] == "challenge"
                                 for row in mine) / rounds,
                "tracks": {track: sum(row["status"] == "pass" and row["track"] == track for row in mine) / rounds
                           for track in TRACKS},
                "unsupported": sum(row["status"] == "unsupported" for row in rows if row["model"] == model),
                "capabilities": capabilities or [],
                **coverage(mine, rounds, capabilities),
                "timeouts": sum(row["status"] == "timeout" for row in mine) / rounds,
                "timeout_s": manifest["options"].get("timeout"),
                "infrastructure_errors": sum(row["status"] in INFRASTRUCTURE_STATUSES for row in mine) / rounds,
                "median_s": statistics.median(times),
                "min_available_kib": min((env["min_mem_available_kib"] for env in envs
                                          if env.get("min_mem_available_kib") is not None), default=None),
                "peak_swap_kib": max((env["peak_swap_used_kib"] for env in envs
                                      if env.get("peak_swap_used_kib") is not None), default=None),
                "oom": any(row["status"] == "out_of_memory" for row in mine)
                       or any(env.get("system_oom_kills") for env in envs),
                "temperature": temperatures.get(model),
                **peak_memory(envs),
                "flag": published.get("flags", {}).get(model),
                "note": published.get("note"),
                **speed(all_rows, model),
            })
    return found


def display_name(model):
    """The name used everywhere in the README; full Ollama names are listed under the full results.

    hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M -> LFM2.5-1.2B-Instruct-GGUF:Q4_K_M, lfm3:latest -> lfm3."""
    name = model.split("/")[-1] if model.startswith("hf.co/") else model
    return name[:-len(":latest")] if name.endswith(":latest") else name


def row_name(entry):
    """Display name, plus the thread note for a result from a different thread count than the rest."""
    note = entry.get("threads_note")
    return display_name(entry["model"]) + (f" ({note})" if note else "")


# Tracks that need a model capability; the runner records "unsupported" for these without one.
CAPABILITY_TRACKS = {"vision": "vision", "agent": "tools"}


def supported_tracks(capabilities):
    return [track for track in TRACKS
            if track not in CAPABILITY_TRACKS or CAPABILITY_TRACKS[track] in capabilities]


def coverage(rows, rounds, capabilities=None):
    """Accuracy over the tracks a model supports, and over the text tracks every model has.

    A case the model can't attempt (no vision, no tools) is "not applicable", never a 0:
    it is left out of that model's accuracy, and the supported tracks are reported alongside.
    Which tracks are supported comes from the model's recorded capabilities, not from which
    rows happen to exist, so a run that left out a track can't pass for a missing capability."""
    cases = {track: sum(row["track"] == track for row in rows) / rounds for track in TRACKS}
    passed = {track: sum(row["track"] == track and row["status"] == "pass" for row in rows) / rounds
              for track in TRACKS}
    if capabilities is None:  # older manifests: fall back to the tracks that have results
        tracks = [track for track in TRACKS if cases[track]]
    else:
        tracks = supported_tracks(capabilities)
        missing = [track for track in tracks if not cases[track]]
        if missing:
            raise ValueError(f"no results for supported track(s) {', '.join(missing)}")
    text = [track for track in tracks if track in TEXT_TRACKS]
    share = lambda chosen: (100 * sum(passed[t] for t in chosen) / sum(cases[t] for t in chosen)
                            if chosen and sum(cases[t] for t in chosen) else None)
    return {"supported_tracks": tracks, "track_cases": cases,
            "accuracy": share(tracks), "text_accuracy": share(text)}


def coverage_label(entry):
    missing = [TRACK_NAMES[track].lower() for track in TRACKS if track not in entry["supported_tracks"]]
    label = f"{len(entry['supported_tracks'])}/{len(TRACKS)}"
    return label + (f" (no {', '.join(missing)})" if missing else "")


def peak_memory(envs):
    """Peak memory in use (RAM + swap, system-wide) over the warm phases, in GiB.

    Runs that recorded zram's own RAM use give an exact per-sample figure. Older runs only
    have the separate peaks of RAM in use and swap in use, so their sum is an approximation:
    the peaks may not coincide, and zram's compressed pages are counted twice."""
    exact = [env["peak_memory_in_use_kib"] for env in envs if env.get("peak_memory_in_use_kib") is not None]
    if exact and len(exact) == len(envs):
        return {"peak_mem_gb": max(exact) / 2**20, "mem_exact": True}
    approx = [(env.get("peak_system_used_estimate_kib") or 0) + (env.get("peak_swap_used_kib") or 0)
              for env in envs if env.get("peak_system_used_estimate_kib")]
    return {"peak_mem_gb": max(approx) / 2**20 if approx else None,
            "mem_exact": not any(env.get("peak_swap_used_kib") for env in envs)}


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
            "sustained_change": statistics.mean(changes) if changes else None,
            **time_split(rows, model)}


def time_split(rows, model):
    """Where a warm case's time goes: reading the prompt (prefill) versus writing the answer.

    Uses Ollama's own durations, summed over a case's turns. Prompt tokens served from the
    cache are left out of the reading speed, since they weren't processed again."""
    cases = []
    for row in rows:
        if row["model"] != model or row["phase"] != "warm":
            continue
        metrics = [turn["metrics"] for turn in row.get("turns", []) if turn.get("metrics")]
        if not metrics:
            continue
        processed = sum((m.get("prompt_eval_count") or 0) - (m.get("prompt_eval_cached_count") or 0) for m in metrics)
        cases.append({
            "read_s": sum(m.get("prompt_eval_duration") or 0 for m in metrics) / 1e9,
            "write_s": sum(m.get("eval_duration") or 0 for m in metrics) / 1e9,
            "processed": processed, "generated": sum(m.get("eval_count") or 0 for m in metrics)})
    if not cases:
        return {"read_s": None}
    read, write = sum(case["read_s"] for case in cases), sum(case["write_s"] for case in cases)
    return {
        "read_s": statistics.median(case["read_s"] for case in cases),
        "write_s": statistics.median(case["write_s"] for case in cases),
        "read_share": read / (read + write) if read + write else None,
        "prefill_tok_s": statistics.median(case["processed"] / case["read_s"] for case in cases
                                           if case["read_s"] and case["processed"] > 0),
        "case_decode_tok_s": statistics.median(case["generated"] / case["write_s"] for case in cases
                                               if case["write_s"] and case["generated"]),
        "answer_tokens": statistics.median(case["generated"] for case in cases),
    }


def combined_temperatures(items):
    """One temperature summary per model across all its rounds (temperature_summary is per round)."""
    by_model = defaultdict(list)
    for item in items:
        by_model[item["model"]].append(item)
    combined = {}
    for model, rounds in by_model.items():
        # Weighted by telemetry samples (about one a second), so the mean is time-weighted; older
        # summaries without sample counts fall back to cases.
        weighted = [(item["mean_cpu_c"], item.get("samples") or item["cases"]) for item in rounds
                    if item["mean_cpu_c"] is not None]
        combined[model] = {
            "mean_cpu_c": (sum(mean * n for mean, n in weighted) / sum(n for _, n in weighted)
                           if weighted else None),
            "peak_cpu_c": max(item["peak_cpu_c"] for item in rounds),
            "throttled_cases": sum(item["throttled_cases"] for item in rounds),
        }
    return combined


def number(value):
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def limit_label(entry):
    seconds = entry.get("timeout_s") or 600
    return f"{seconds / 60:g}-minute" if seconds >= 60 else f"{seconds:g}-second"


def duration(seconds):
    return f"{seconds:.1f} s" if seconds < 60 else f"{seconds / 60:.1f} min"


def memory(entry):
    available = entry["min_available_kib"]
    if available is None or entry["peak_mem_gb"] is None or not entry["device"].startswith("Raspberry"):
        return "—"
    free = f"{available / 2**20:.1f} GB" if available >= 2**20 else f"{available / 1024:.0f} MB"
    figure = f"{entry['peak_mem_gb']:.1f} GB{'' if entry['mem_exact'] else '≈'} ({free} free)"
    if entry["flag"]:
        return f"❌ {figure}"  # the incident itself is noted in the glance table and charts
    if entry["oom"]:
        return f"❌ {figure}: out of memory"
    swap = entry["peak_swap_kib"] or 0
    if available < 2**20 // 2 or swap > 2**20:
        return f"⚠️ {figure}"
    return f"✅ {figure}"


def table(group, totals):
    # Short headers split over two lines (GitHub allows <br> in cells), so the columns stay narrow.
    head = ["Model", "Accuracy<br>(all)", "Accuracy<br>(text)", "Challenge",
            *(TRACK_NAMES[track] for track in TRACKS),
            "Time per<br>case", "Timed<br>out", "Peak memory<br>(lowest free)", "CPU °C<br>mean / peak"]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] + ["---:"] * (len(head) - 3) + ["---", "---"]) + "|"]
    for entry in sorted(group, key=lambda item: (-item["accuracy"], item["median_s"])):
        temperature = entry["temperature"]
        peak = "—"
        if temperature:
            mean = temperature.get("mean_cpu_c")
            peak = f"{mean:.0f} / {temperature['peak_cpu_c']:.0f}" if mean is not None else \
                f"— / {temperature['peak_cpu_c']:.0f}"
            if temperature["throttled_cases"]:
                peak += f" ⚠️ {temperature['throttled_cases']} throttled"
        lines.append("| " + " | ".join([
            f"`{display_name(entry['model'])}`" + (f" ({entry['threads_note']})" if entry.get("threads_note") else "")
            + ("" if entry["setup"] else " †"),
            (f"**{entry['accuracy']:.0f}%**" if len(entry["supported_tracks"]) == len(TRACKS)
             else coverage_label(entry).split("(")[-1].rstrip(")")), f"{entry['text_accuracy']:.0f}%",
            number(entry["challenge"]),
            *(number(entry["tracks"][track]) if track in entry["supported_tracks"] else "n/a" for track in TRACKS),
            duration(entry["median_s"]), number(entry["timeouts"]), memory(entry), peak]) + " |")
    return "\n".join(lines)


def rate(value):
    return f"{value:.1f} tokens/s" if value else "—"


def speed_table(group):
    lines = ["| Model | First load from storage | Prompt processing | Generation (in cases) | "
             "Generation (sustained) | Sustained drift |",
             "|---|---:|---:|---:|---:|---:|"]
    for entry in sorted(group, key=lambda item: item["median_s"]):
        load = f"{entry['first_load_s']:.0f} s" if entry["first_load_s"] is not None else "—"
        change = f"{entry['sustained_change'] * 100:+.1f}%" if entry["sustained_change"] is not None else "—"
        lines.append(f"| `{row_name(entry)}` | {load} | {rate(entry.get('prefill_tok_s'))} | "
                     f"{rate(entry.get('case_decode_tok_s'))} | {rate(entry['sustained_tok_s'])} | {change} |")
    return "\n".join(lines)


def threads_tables(pairs):
    """Two short tables, speed and outcomes, one row per model and mode.

    Each cell reads from fewer threads to more ("19.5 → 11.8 s 🟢 −40%"), so the order is
    unambiguous. GitHub strips colour from Markdown, so a marker carries better/worse: 🟢 an
    improvement, 🔴 a change for the worse. The sign alone can't, since for time a minus is good
    and for speed a plus is. Splitting into two tables keeps each one narrow enough that GitHub
    doesn't squeeze its columns into one word per line."""
    def marker(a, b, higher_is_better):
        return "🟢" if (b > a) == higher_is_better else "🔴"

    def change(a, b, fmt, unit, higher_is_better):
        if a is None or b is None:
            return "—"
        text = f"{fmt(a)} → {fmt(b)}{unit}"
        if a and abs(b / a - 1) >= 0.005:
            text += f" {marker(a, b, higher_is_better)} {(b / a - 1) * 100:+.0f}%"
        return text

    def shift(a, b, fmt, unit, higher_is_better):
        if a is None or b is None:
            return "—"
        text = f"{fmt(a)} → {fmt(b)}{unit}"
        return text + (f" {marker(round(a), round(b), higher_is_better)}" if round(a) != round(b) else "")
    temp = lambda entry: entry["temperature"]["peak_cpu_c"] if entry["temperature"] else None
    rows = sorted(pairs, key=lambda pair: (pair[0]["thinking"], pair[0]["median_s"]))
    speed = ["| Model | Thinking | Time per case | Prompt processing | Generation | Generation (sustained) |",
             "|---|---|---:|---:|---:|---:|"]
    outcome = ["| Model | Thinking | Accuracy | Timed out | Peak CPU |", "|---|---|---:|---:|---:|"]
    tokens = lambda v: f"{v:.1f}"
    for a, b in rows:
        name = f"| `{display_name(a['model'])}` | {a['thinking']}"
        speed.append(" | ".join([name,
            change(a["median_s"], b["median_s"], lambda v: duration(v).replace(" min", "").replace(" s", ""),
                   " min" if b["median_s"] >= 60 else " s", higher_is_better=False)
            if (a["median_s"] >= 60) == (b["median_s"] >= 60)
            else change(a["median_s"], b["median_s"], duration, "", higher_is_better=False),
            change(a.get("prefill_tok_s"), b.get("prefill_tok_s"), tokens, "", higher_is_better=True),
            change(a.get("case_decode_tok_s"), b.get("case_decode_tok_s"), tokens, "", higher_is_better=True),
            change(a.get("sustained_tok_s"), b.get("sustained_tok_s"), tokens, "", higher_is_better=True)]) + " |")
        outcome.append(" | ".join([name,
            shift(a["accuracy"], b["accuracy"], lambda v: f"{v:.0f}", "%", higher_is_better=True),
            shift(a["timeouts"], b["timeouts"], number, "", higher_is_better=False),
            shift(temp(a), temp(b), lambda v: f"{v:.0f}", " °C", higher_is_better=False)]) + " |")
    return "\n".join(speed), "\n".join(outcome)


def time_table(group):
    lines = ["| Model | Median time per case | Reading the prompt | Writing the answer | Share spent reading | "
             "Answer length (median) |", "|---|---:|---:|---:|---:|---:|"]
    for entry in sorted(group, key=lambda item: item["median_s"]):
        lines.append(f"| `{row_name(entry)}` | {duration(entry['median_s'])} | {duration(entry['read_s'])} | "
                     f"{duration(entry['write_s'])} | {entry['read_share']:.0%} | {entry['answer_tokens']:.0f} tokens |")
    return "\n".join(lines)


def svg_chart(points, theme, title):
    """Accuracy against speed. Bubble area shows peak memory in use, colour shows the thinking
    mode, and a thin connector joins each model's thinking-off and thinking-on points."""
    t = THEMES[theme]
    width, height = 920, 540
    left, top, plot_w, plot_h = 68, 104, 560, 350
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
    # Finer round ticks than the other charts, so the axis can hug the data and spread the bubbles out.
    fine = (1, 2, 5, 10, 20, 30, 60, 120, 300, 600, 1200)
    low = max((tick for tick in fine if tick <= min(times) / 1.25), default=fine[0])
    high = min((tick for tick in fine if tick >= max(times) * 1.25), default=fine[-1])
    ticks = [tick for tick in fine if low <= tick <= high]

    def x(seconds):
        return left + plot_w * (math.log10(seconds) - math.log10(low)) / (math.log10(high) - math.log10(low))

    def y(percent):
        return top + plot_h * (1 - percent / 100)

    def accuracy(point):
        # Over the tracks the model supports, as in the tables.
        return point["accuracy"]

    def text(px, py, content, size=13, fill="ink2", anchor="start", weight="normal", halo=False):
        # A halo in the surface colour keeps labels legible where they cross a connector.
        outline = f'stroke="{t["surface"]}" stroke-width="4" paint-order="stroke" ' if halo else ""
        return (f'<text x="{px:.1f}" y="{py:.1f}" font-size="{size}" font-weight="{weight}" '
                f'text-anchor="{anchor}" fill="{t[fill]}" {outline}{font}>{content}</text>')

    subtitle = "Higher is more accurate, further left is faster. Colour: thinking mode"
    subtitle += "; bubble area: peak memory in use (RAM + zram)." if with_memory else "."
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
           f'<rect width="{width}" height="{height}" rx="8" fill="{t["surface"]}"/>',
           text(24, 36, title, size=19, fill="ink", weight="700"),
           text(24, 60, subtitle, size=13.5, fill="ink2")]
    for percent in range(0, 101, 20):
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y(percent):.1f}" y2="{y(percent):.1f}" '
                   f'stroke="{t["grid"] if percent else t["axis"]}" stroke-width="1"/>')
        out.append(text(left - 10, y(percent) + 4, f"{percent}%", size=12.5, anchor="end"))
    for tick in ticks:
        out.append(f'<line x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{top}" y2="{top + plot_h}" '
                   f'stroke="{t["grid"]}" stroke-width="1"/>')
        label = f"{tick:g} s" if tick < 60 else f"{tick / 60:g} min"
        out.append(text(x(tick), top + plot_h + 20, label, size=12.5, anchor="middle"))
    out.append(text(left + plot_w / 2, top + plot_h + 46, "Median time per case (log scale)", size=14, fill="ink",
                    weight="700", anchor="middle"))
    out.append(text(left - 44, top - 14, "Accuracy (cases passed on supported tracks)", size=14, fill="ink", weight="700"))
    out.append(text(left + plot_w - 6, top + 18, "better ↖", size=13, anchor="end"))

    # Connectors first, so the bubbles sit on top of them.
    by_model = defaultdict(dict)
    for point in points:
        by_model[point["model"]][point["thinking"]] = point
    for modes in by_model.values():
        if "off" in modes and "on" in modes:
            a, b = modes["off"], modes["on"]
            out.append(f'<line x1="{x(a["median_s"]):.1f}" y1="{y(accuracy(a)):.1f}" x2="{x(b["median_s"]):.1f}" '
                       f'y2="{y(accuracy(b)):.1f}" stroke="{t["ink2"]}" stroke-width="1.75" stroke-dasharray="1 4" '
                       f'stroke-linecap="round"/>')
    # Largest bubbles first, so small ones stay visible on top.
    bubbles = []
    for point in sorted(points, key=lambda item: -radius(item)):
        px, py, r = x(point["median_s"]), y(accuracy(point)), radius(point)
        bubbles.append((point, px, py, r))
        color = t["on" if point["thinking"] == "on" else "off"]
        dashed = ' stroke-dasharray="4 3"' if point["flag"] else ""
        ring = t["ink2"] if point["flag"] else t["surface"]
        out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{r:.1f}" fill="{color}" fill-opacity="0.9" '
                   f'stroke="{ring}" stroke-width="2"{dashed}/>')

    def hits_circle(box, cx, cy, r):
        x0, y0, x1, y1 = box
        nearest_x, nearest_y = min(max(cx, x0), x1), min(max(cy, y0), y1)
        return (nearest_x - cx) ** 2 + (nearest_y - cy) ** 2 < r * r

    # Each label tries right, left, above and below its bubble, taking the first spot that
    # stays inside the plot and clear of every bubble and earlier label.
    placed, leaders, labels = [], [], []
    for point, px, py, r in sorted(bubbles, key=lambda item: item[2]):
        label = f'{display_name(point["model"])} · {accuracy(point):.0f}%'
        # Flags and a different thread count go on a second line under the name, so the label stays narrow.
        notes = (["hung the Pi"] if point["flag"] else []) + ([point["threads_note"]] if point.get("threads_note") else [])
        note = f"({', '.join(notes)})" if notes else None
        # Width estimates for 14 px semibold labels and 12.5 px notes, on the generous side.
        w = max(8.2 * len(label), 7.2 * len(note or ""))
        extra = 15 if note else 0  # height of the second line
        candidates = [(px + r + 8, py + 4, "start"), (px - r - 8, py + 4, "end"),
                      (px, py - r - 8, "middle"), (px, py + r + 18, "middle"),
                      (px + r * 0.7 + 6, py - r * 0.7 - 6, "start"), (px - r * 0.7 - 6, py - r * 0.7 - 6, "end"),
                      (px + r * 0.7 + 6, py + r * 0.7 + 16, "start"), (px - r * 0.7 - 6, py + r * 0.7 + 16, "end"),
                      (px, py - r - 26, "middle"), (px, py + r + 36, "middle")]

        def box(cand):
            lx, ly, anchor = cand
            x0 = lx if anchor == "start" else lx - w if anchor == "end" else lx - w / 2
            return (x0, ly - 12, x0 + w, ly + 4 + extra)

        def overlaps(cand):
            """How badly a spot collides: leaving the plot counts most, then bubbles, then labels."""
            x0, y0, x1, y1 = box(cand)
            outside = not (left <= x0 and x1 <= left + plot_w + 30 and top <= y0 and y1 <= top + plot_h)
            circles = sum(hits_circle((x0, y0, x1, y1), cx, cy, cr) for other, cx, cy, cr in bubbles if other is not point)
            labels = sum(x0 < b[2] and b[0] < x1 and y0 < b[3] and b[1] < y1 for b in placed)
            return 100 * outside + 10 * circles + labels
        # The first clear spot wins; if none is clear, take the least crowded one.
        choice = min(candidates, key=overlaps)
        placed.append(box(choice))
        # A label pushed away from its bubble gets a thin leader line, so it can't be mistaken for a neighbour's.
        x0, y0, x1, y1 = box(choice)
        nearest_x, nearest_y = min(max(px, x0), x1), min(max(py, y0), y1)
        gap = math.hypot(nearest_x - px, nearest_y - py) - r
        if gap > 10:
            ux, uy = (nearest_x - px) / (gap + r), (nearest_y - py) / (gap + r)
            leaders.append(f'<line x1="{px + ux * (r + 2):.1f}" y1="{py + uy * (r + 2):.1f}" x2="{nearest_x - ux * 3:.1f}" '
                           f'y2="{nearest_y - uy * 3:.1f}" stroke="{t["ink2"]}" stroke-width="1"/>')
        labels.append(text(choice[0], choice[1], label, size=14, fill="ink", weight="600", anchor=choice[2], halo=True))
        if note:
            labels.append(text(choice[0], choice[1] + 16, note, size=12.5, fill="ink2", anchor=choice[2], halo=True))
    out += leaders + labels

    # Legend column: thinking modes, then memory sizes, then the flag marker.
    ly = top + 6
    modes = [mode for mode in ("off", "on") if any(point["thinking"] == mode for point in points)]
    out.append(text(legend_x, ly, "Thinking", size=14, fill="ink", weight="700"))
    for mode in modes:
        ly += 24
        out.append(f'<circle cx="{legend_x + 7}" cy="{ly - 4}" r="7" fill="{t[mode]}"/>')
        out.append(text(legend_x + 22, ly, mode, fill="ink"))
    if with_memory:
        ly += 34
        out.append(text(legend_x, ly, "Memory in use", size=14, fill="ink", weight="700"))
        for gb in (2, 4, 8):
            r = scale * math.sqrt(gb)
            ly += 2 * r + 6
            out.append(f'<circle cx="{legend_x + 22}" cy="{ly - r:.1f}" r="{r:.1f}" fill="none" '
                       f'stroke="{t["ink2"]}" stroke-width="1.5"/>')
            out.append(text(legend_x + 54, ly - r + 4, f"{gb} GB", fill="ink"))
        for offset, line in enumerate(("For scale: the Pi has", "8 GB of RAM and", "2 GB of zram.")):
            out.append(text(legend_x, ly + 24 + 17 * offset, line, size=12.5))
        ly += 58
    if any(point["flag"] for point in points):
        ly += 34
        out.append(f'<circle cx="{legend_x + 9}" cy="{ly - 4}" r="8" fill="none" stroke="{t["ink2"]}" '
                   f'stroke-width="2" stroke-dasharray="4 3"/>')
        out.append(text(legend_x + 24, ly, "Didn't reliably fit", fill="ink"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def picture(base, alt, width=920):
    """README markup for a chart: light and dark versions, plus phone versions if they exist.

    The browser takes the first <source> whose media query matches, so the narrow-screen
    sources come first. Screens under 600 px get the one-column phone chart, which would
    otherwise be shrunk to under half size."""
    sources = []
    if (ASSETS / f"{base}-mobile-light.svg").exists():
        sources += [f'  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" '
                    f'srcset="docs/assets/{base}-mobile-dark.svg">',
                    f'  <source media="(max-width: 600px)" srcset="docs/assets/{base}-mobile-light.svg">']
    return ["<picture>", *sources,
            f'  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/{base}-dark.svg">',
            f'  <img alt="{alt}" src="docs/assets/{base}-light.svg" width="{width}">',
            "</picture>", ""]


def write_chart(base, render, render_mobile=None):
    for theme in THEMES:
        (ASSETS / f"{base}-{theme}.svg").write_text(render(theme))
        if render_mobile:
            (ASSETS / f"{base}-mobile-{theme}.svg").write_text(render_mobile(theme))
    return base


def short_memory(entry):
    if entry["peak_mem_gb"] is None:
        return "—"
    icon = "❌" if entry["flag"] or entry["oom"] else "⚠️" if "⚠️" in memory(entry) else "✅"
    return f"{icon} {entry['peak_mem_gb']:.1f} GB{'' if entry['mem_exact'] else '≈'}"


def glance_table(entries, total):
    lines = ["| Model | Thinking | Accuracy | Tracks | Time per case | Memory | Note |", "|---|---|---:|---|---:|---|---|"]
    for entry in entries:
        notes = [entry["flag"]] if entry["flag"] else []
        if entry["timeouts"]:
            count = number(entry["timeouts"])
            notes.append(f"{count} case{'' if count == '1' else 's'} hit the {limit_label(entry)} limit")
        note = "; ".join(notes)
        lines.append(f"| `{display_name(entry['model'])}`{' (' + entry['threads_note'] + ')' if entry.get('threads_note') else ''} | {entry['thinking']} | **{entry['accuracy']:.0f}%** | "
                     f"{coverage_label(entry)} | {duration(entry['median_s'])} | {short_memory(entry)} | {note} |")
    return "\n".join(lines)


def dumbbell_rows(entries, total):
    by_model = defaultdict(dict)
    for entry in entries:
        by_model[entry["model"]][entry["thinking"]] = entry
    rows = []
    for model, modes in by_model.items():
        flag = next((entry["flag"] for entry in modes.values() if entry["flag"]), None)
        # Short badges under the model name; a missing orange dot already shows "not run".
        any_entry = next(iter(modes.values()))
        badges = ["hung the Pi"] if flag else []
        badges += sorted({entry["threads_note"] for entry in modes.values() if entry.get("threads_note")})
        if "on" not in modes:
            badges.append("thinking off only" if "thinking" in any_entry["capabilities"] else "no thinking mode")
        badges += [f"no {TRACK_NAMES[t].lower()}" for t in TRACKS if t not in any_entry["supported_tracks"]]
        rows.append({"model": display_name(model), "badges": badges,
                     **{mode: ({"accuracy": modes[mode]["accuracy"], "median_s": modes[mode]["median_s"]}
                               if mode in modes else None) for mode in ("off", "on")}})
    return sorted(rows, key=lambda row: -max(row[mode]["accuracy"] for mode in ("off", "on") if row[mode]))


def heatmap_rows(entries, totals):
    return [{"label": row_name(entry), "mode": entry["thinking"],
             "values": [100 * entry["tracks"][track] / entry["track_cases"][track]
                        if track in entry["supported_tracks"] else None for track in TRACKS]
                       + [entry["accuracy"]]} for entry in entries]


def slug(text):
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-").replace("--", "-")


def check_duplicates(found):
    """Two official runs of the same model, mode and thread count on one device would both appear
    in the tables and pair up twice; refuse rather than pick one silently."""
    seen = {}
    for entry in found:
        if not entry["setup"]:
            continue
        key = (entry["device"], entry["model"], entry["thinking"], entry["threads"])
        if key in seen:
            sys.exit(f"{seen[key]} and {entry['run']} are both official runs of {entry['model']}, thinking "
                     f"{entry['thinking']}, {entry['threads']} threads, on {entry['device']}. Keep one.")
        seen[key] = entry["run"]


def build():
    found = entries()
    check_duplicates(found)
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
        # The main tables and chart use one thread count; other thread counts go to a comparison table.
        main = [entry for entry in by_device[name] if entry["threads"] == PRIMARY_THREADS]
        official = {(entry["model"], entry["thinking"]) for entry in main if entry["setup"]}
        group = [entry for entry in main if entry["setup"] or (entry["model"], entry["thinking"]) not in official]
        # A model and mode with official results only at another thread count (gemma4:e4b, which hung
        # the Pi and wasn't rerun) still appears, clearly marked, rather than silently disappearing.
        fallback = {}
        for entry in by_device[name]:
            key = (entry["model"], entry["thinking"])
            if entry["setup"] and entry["threads"] != PRIMARY_THREADS and key not in official:
                fallback.setdefault(key, entry)["threads_note"] = f"{entry['threads']} threads only"
        group += list(fallback.values())
        others = [entry for entry in by_device[name] if entry["threads"] != PRIMARY_THREADS and entry["setup"]
                  and entry not in fallback.values()]
        parts += [f"### {name}", ""]
        setups = sorted({entry["setup"] for entry in group if entry["setup"]})
        for setup in setups:
            parts += [f"Setup: {setup}", ""]
        photos = [(path, alt) for path, alt in HARDWARE_PHOTOS if (ROOT / path).exists()]
        if name.startswith("Raspberry") and photos:
            parts += ["<p>" + " ".join(f'<a href="{path}"><img src="{path}" alt="{alt}" height="220"></a>'
                                       for path, alt in photos) + "</p>", ""]
        if any(not entry["setup"] for entry in group):
            parts += ["† Exploratory run: one pass, no recorded hardware setup. "
                      "[Official runs](docs/OFFICIAL_RUNS.md) will replace these rows.", ""]
        slug_name = slug(name)
        official = sorted((entry for entry in group if entry["setup"]), key=lambda entry: -entry["accuracy"])
        # Each pair is (fewer threads, more threads), whichever count is the main one.
        pairs = [tuple(sorted((base_entry, other), key=lambda entry: entry["threads"]))
                 for other in others for base_entry in group
                 if base_entry["setup"] and base_entry["threads"] == PRIMARY_THREADS
                 and (base_entry["model"], base_entry["thinking"]) == (other["model"], other["thinking"])]
        total = totals["all"]
        if official:
            parts += ["### At a glance", "", glance_table(official, total), "",
                      "Accuracy is the share of cases passed on the tracks the model supports; Tracks shows how many "
                      "of the 5 that is. A track a model can't attempt (no vision, for example) is not counted as "
                      "0%. Time is the median per case. Memory is the peak in use, RAM plus zram, against the Pi's "
                      "7.9 GB of usable RAM.", ""]
            parts += picture(write_chart(f"thinking-{slug_name}", lambda theme: charts.dumbbell(
                dumbbell_rows(official, total), theme, "What thinking buys"),
                lambda theme: charts.dumbbell_mobile(dumbbell_rows(official, total), theme, "What thinking buys")),
                "Accuracy and median time per case for each model with thinking off and on")
            parts += ["### Strengths and weaknesses by track", ""]
            columns = [TRACK_NAMES[track] for track in TRACKS] + ["All"]
            parts += picture(write_chart(f"tracks-{slug_name}", lambda theme: charts.heatmap(
                heatmap_rows(official, totals), columns, theme, "Where each model succeeds"),
                lambda theme: charts.heatmap_mobile(heatmap_rows(official, totals), columns, theme,
                                                    "Where each model succeeds")),
                "Share of each track's cases passed, per model and thinking mode")
        if pairs:
            low, high = pairs[0][0]["threads"], pairs[0][1]["threads"]
            parts += [f"### {low} vs {high} CPU threads", ""]
            thread_rows = [{"label": f"{display_name(a['model'])} · thinking {a['thinking']}", "mode": a["thinking"],
                            "before": a["median_s"], "after": b["median_s"]}
                           for a, b in sorted(pairs, key=lambda pair: (pair[0]["thinking"], pair[0]["model"]))]
            parts += picture(write_chart(f"threads-{slug_name}",
                lambda theme: charts.threads(thread_rows, theme, "Does using all four cores help?", low, high,
                                             PRIMARY_THREADS),
                lambda theme: charts.threads_mobile(thread_rows, theme, "Do all four cores help?", low, high,
                                                    PRIMARY_THREADS)),
                f"Median time per case with {low} and {high} threads per model and thinking mode")
            parts += [f"The main results use {PRIMARY_THREADS} threads, all of the Pi's cores. Compared with "
                      f"{low} threads, prompt processing is faster, but generation can be slower; details are in "
                      "the full results below.", ""]

        # Everything else stays available, one click away.
        parts += ["### Full results", "", "<details>",
                  "<summary>All tables: per-track scores, speed, where the time goes, memory, temperature, "
                  "threads, and the accuracy, speed and memory chart</summary>", ""]
        parts += ["#### Accuracy, speed and memory", ""]
        base = f"score-vs-time-{slug_name}"
        for theme in THEMES:
            (ASSETS / f"{base}-{theme}.svg").write_text(svg_chart(group, theme, f"Accuracy and speed on {name}"))
        parts += picture(base, f"Cases passed against median time per case on {name}; colour shows the thinking "
                               "mode and bubble area the peak memory in use")
        for note in sorted({entry["note"] for entry in group if entry["note"]}):
            parts += [f"Note: {note}", ""]
        renamed = sorted({(display_name(entry["model"]), entry["model"]) for entry in group
                          if display_name(entry["model"]) != entry["model"] and entry["model"].startswith("hf.co/")})
        if renamed:
            parts += ["Ollama names, for `ollama pull`: " + "; ".join(f"`{short}` is `{full}`" for short, full in renamed), ""]
        for mode in ("off", "on"):
            subset = [entry for entry in group if entry["thinking"] == mode]
            if subset:
                rounds = sorted({entry["rounds"] for entry in subset})
                note = (f"Challenge counts are out of {totals['challenge']} cases and each track out of "
                        f"{totals['arithmetic']}.")
                if any(len(entry["supported_tracks"]) < len(TRACKS) for entry in subset):
                    note += (" A model without vision or tools has fewer challenge cases, and its unsupported "
                             "tracks show n/a.")
                if rounds != [1]:
                    note = f"Scores are averaged over {'/'.join(map(str, rounds))} passes. " + note
                parts += [f"#### Thinking {mode}: scores by track", "", table(subset, totals), "", note, ""]
        if any(entry["peak_mem_gb"] is not None for entry in group):
            parts += [MEMORY_NOTE, ""]
        timed = [entry for entry in group if entry["thinking"] == "off"
                 and (entry["first_load_s"] is not None or entry["sustained_tok_s"])]
        if timed:
            parts += ["#### Speed (thinking off)", "", speed_table(timed), "", SPEED_NOTE, ""]
        split = [entry for entry in group if entry["thinking"] == "off" and entry.get("read_s") is not None
                 and entry["setup"]]
        if split:
            parts += ["#### Where the time per case goes (thinking off)", "", time_table(split), "", TIME_NOTE, ""]
        if pairs:
            speed_rows, outcome_rows = threads_tables(pairs)
            low_t, high_t = pairs[0][0]["threads"], pairs[0][1]["threads"]
            parts += [f"#### {low_t} → {high_t} threads: speed", "",
                      "Prompt processing and generation are in tokens/s.", "", speed_rows, "",
                      f"#### {low_t} → {high_t} threads: outcomes", "", outcome_rows, "",
                      THREADS_NOTE.format(low=low_t, high=high_t, limit=limit_label(pairs[0][1])), ""]
        parts += ["</details>", ""]
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
    add_command.add_argument("--note", help="why some rounds are left out, or why errors are accepted; "
                                            "shown with the results")
    add_command.add_argument("--allow-errors", action="store_true",
                             help="publish although some cases failed with server or connection errors "
                                  "(needs --note)")
    add_command.add_argument("--flag", action="append", default=[], metavar="MODEL=TEXT",
                             help="a warning shown in that model's memory column, e.g. a hang")
    commands.add_parser("build", help="rebuild the README results section and charts")
    args = parser.parse_args()
    if args.command == "add":
        add(args.run, args.name, args.rounds, args.note, args.flag, args.allow_errors)
    else:
        build()


if __name__ == "__main__":
    main()
