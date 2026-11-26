#!/usr/bin/env python3
"""Publish benchmark runs and rebuild the README results section.

  python3 tools/publish.py add results/<run> [--name NAME]
      Copy a finished run into docs/results/NAME so it can be committed.
      (results/ itself is gitignored.)

  python3 tools/publish.py build
      Rebuild the README section between the results markers, and the
      light/dark charts in docs/assets/, from the published runs of the
      current suite, or of the previous suite until the current one has any.

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
from light_eval import (CURRENT_SUITE, INFRASTRUCTURE_STATUSES, SUITE_PROTOCOLS, SUITES, apply_budget_defaults,  # noqa: E402
                        build_parser, hashed_paths, infrastructure_errors, read_rows, suite_module, suite_of,
                        summarize, temperature_summary, write_json)
import charts  # noqa: E402  (tools/charts.py)
from runtime import recordable_setting  # noqa: E402

PUBLISHED = ROOT / "docs" / "results"
ASSETS = ROOT / "docs" / "assets"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
TRACK_NAMES = {"arithmetic": "Arithmetic", "structured": "Structured", "agent": "Agent",
               "vision": "Vision", "context": "Context", "rag": "RAG", "instructions": "Instructions",
               "code": "Code"}
TIERS = ("control", "challenge", "hard")

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
# Tracks that need images; "text accuracy" covers every other track.
IMAGE_TRACKS = ("vision",)
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
    "(`--threads 4`). \"In cases\" is generation during the {cases} cases, "
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
PROTOCOL_OPTIONS = ("context", "max_tokens", "timeout", "tool_rounds", "tool_calls", "sustain_seconds",
                    "sustain_tokens", "seed")
# Thinking off measures load time and sustained speed as well; thinking on is warm only (see
# docs/OFFICIAL_RUNS.md), since those measure the hardware, not the thinking mode.
STANDARD_PHASES = {"off": {"cold", "warm", "sustained"}, "on": {"warm"}}


def standard_options(thinking, suite=CURRENT_SUITE):
    args = apply_budget_defaults(build_parser().parse_args(["run", "--models", "model", "--thinking", thinking,
                                                            "--suite", suite]))
    return {key: getattr(args, key) for key in PROTOCOL_OPTIONS if hasattr(args, key)}


def tracks_of(suite):
    return tuple(suite_module(suite).TRACKS)


def case_files(suite):
    """The files that define a suite's cases and scoring, whose hashes a published run must match.
    The runner's own code (light_eval.py, runtime.py) is covered by the protocol version instead."""
    if suite == "1.7":
        return [ROOT / "cases.py", *(path for path in hashed_paths("1.7") if path.suffix == ".png")]
    return [ROOT / "cases.py", ROOT / "code_runner.py", *sorted(ROOT.glob("suite_1_8*.py")),
            *(path for path in hashed_paths(suite) if "v1_8" in path.parts)]


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


def run_problems(manifest, rows, keep=None, allow_errors=False, blocks=()):
    """Everything that would make a run's results mean something different from the published ones.

    Returns a list of problems; an empty list means the run can be published as it is."""
    problems = []
    options = manifest.get("options", {})
    current = suite_module(CURRENT_SUITE).VERSION
    suite = suite_of(manifest)
    if suite != CURRENT_SUITE:
        problems.append(f"suite {manifest.get('suite_version')}, but the current suite is {current}")
    # The remaining checks follow the run's own suite, so a refused run lists everything at once.
    suite = suite or CURRENT_SUITE
    if suite == CURRENT_SUITE and manifest.get("protocol_version") != SUITE_PROTOCOLS[suite]:
        problems.append(f"protocol {manifest.get('protocol_version')}, but suite {suite} runs use protocol "
                        f"{SUITE_PROTOCOLS[suite]}")
    if list(options.get("tracks") or []) != list(tracks_of(suite)):
        problems.append(f"ran only some tracks ({', '.join(options.get('tracks') or [])}); a published run "
                        "needs every track, or a missing track reads as a missing capability")
    if options.get("thinking") not in ("off", "on"):
        problems.append(f"thinking {options.get('thinking')!r}; published runs set it explicitly, off or on")
    else:
        for key, value in standard_options(options["thinking"], suite).items():
            if key in options and options[key] != value:
                problems.append(f"--{key.replace('_', '-')} {options[key]} instead of the standard {value}")
        phases = set(options.get("phases") or [])
        official = bool((options.get("setup") or "").strip())
        if "warm" not in phases:
            problems.append(f"phases {', '.join(sorted(phases)) or 'none'}: no warm phase, so there are no scores")
        elif official and phases != STANDARD_PHASES[options["thinking"]]:
            # Exploratory runs (no --setup) may be warm only; official ones follow the protocol.
            problems.append(f"phases {', '.join(sorted(phases)) or 'none'}; thinking-{options['thinking']} runs use "
                            f"{', '.join(sorted(STANDARD_PHASES[options['thinking']]))}")
    if str(manifest.get("server_prompt_cache_ram_mib")) != "0":
        problems.append(f"the server's LLAMA_ARG_CACHE_RAM was {manifest.get('server_prompt_cache_ram_mib')!r}, "
                        "not 0, so memory grew with every case")
    hashes = manifest.get("sha256", {})
    for path in case_files(suite):
        relative = str(path.relative_to(ROOT))
        if hashes.get(relative) != file_hash(path):
            problems.append(f"{relative} differs from the version this run used")
    errors = defaultdict(int)
    for model in sorted({row["model"] for row in rows} | {block["model"] for block in blocks}):
        # The runner's own count, per model: failed cases, plus failed sustained warmups (blocks only).
        counted = infrastructure_errors([row for row in rows if row["model"] == model],
                                        [block for block in blocks if block["model"] == model])
        for status, count in counted.items():
            errors[f"{model}: {status}"] += count
    if errors and not allow_errors:
        problems.append("cases failed for infrastructure reasons, which would count against the model: "
                        + ", ".join(f"{key} ×{count}" for key, count in sorted(errors.items()))
                        + ". Rerun, or pass --allow-errors with a --note if they are the model's own failures")
    # Whether each model really ran in the mode its label says. Only cases with model turns count:
    # unsupported tracks and failed warmups (out of memory, server errors) have none to judge by.
    thought, total = defaultdict(int), defaultdict(int)
    for row in rows:
        if row["phase"] == "warm" and row.get("turns"):
            total[row["model"]] += 1
            thought[row["model"]] += any((turn.get("thinking") or "").strip() for turn in row["turns"])
    for model, count in sorted(total.items()):
        if options.get("thinking") == "on" and thought[model] < count / 2:
            # Ollama flags "thinking" from the chat template, not the training: an Instruct model can
            # carry the flag and still never reason, which would label a thinking-off result "on".
            problems.append(f"{model} produced thinking text in only {thought[model]} of {count} warm "
                            "cases with answers, so it doesn't really have a thinking mode")
        if options.get("thinking") == "off" and thought[model] > 2:
            problems.append(f"{model} produced thinking text in {thought[model]} of {count} warm cases "
                            "although thinking was off, so this isn't a thinking-off result")
    expected = len(manifest.get("case_metadata") or {})
    warm = defaultdict(int)
    for row in rows:
        if row["phase"] == "warm":
            warm[row["model"], row["round"]] += 1
    repeated = defaultdict(set)
    for row in rows:
        if row["phase"] == "repeat":
            repeated[row["model"], row["round"]].add(row["case"])
    official = bool((options.get("setup") or "").strip())
    for model in manifest.get("models", {}):
        if not any(m == model for m, r in warm):
            problems.append(f"{model} has no warm cases")
            continue
        for round_num in sorted(keep or {r for m, r in warm}):
            if warm.get((model, round_num), 0) != expected:
                problems.append(f"round {round_num} of {model} has {warm.get((model, round_num), 0)} of "
                                f"{expected} warm cases")
            plan = manifest.get("plan") or []
            sample = set(plan[round_num - 1].get("repeat", ())) if 0 < round_num <= len(plan) else set()
            if official and suite != "1.7" and (not sample or repeated[model, round_num] != sample):
                # Protocol 4 reports how often outcomes change on a repeat; an official run needs it.
                problems.append(f"round {round_num} of {model} repeated {len(repeated[model, round_num])} of the "
                                f"{len(sample)} cases in the repeatability sample")
    return problems


def add(run, name, rounds=None, note=None, flags=(), allow_errors=False):
    manifest = json.loads((run / "manifest.json").read_text())
    if manifest.get("status") not in ("complete", "complete_with_errors") and not rounds:
        sys.exit(f"{run} is not a complete run (status {manifest.get('status')!r}). To publish only its "
                 "complete rounds, pass --rounds and a --note saying why the rest are left out.")
    if allow_errors and not note:
        sys.exit("--allow-errors needs a --note saying why the errors are acceptable")
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
    kept_blocks = [json.loads(line) for line in files["blocks.jsonl"]]
    problems = run_problems(manifest, kept_rows, keep, allow_errors, kept_blocks)
    if problems:
        sys.exit(f"{run} can't be published as it is:\n" + "\n".join(f"  - {problem}" for problem in problems))
    target.mkdir(parents=True)
    for file, lines in files.items():
        (target / file).write_text("".join(line + "\n" for line in lines))
    published = {}
    if keep is not None:
        # The run's own status stays as recorded; this records what was published and why.
        # Recorded in the manifest only; the README shows a note just when one is given.
        published.update({"rounds": sorted(keep), "original_status": manifest.get("status"),
                          "original_rounds": sorted({row["round"] for row in read_rows(run / "cases.jsonl")})})
    if note:
        published["note"] = note
    if allow_errors:
        published["errors_accepted"] = True
    if parsed_flags:
        published["flags"] = parsed_flags
    if published:
        manifest["published"] = published
    if manifest.get("server_environment"):
        # Protocol 2 recorded every OLLAMA_*/LLAMA_ARG_* variable; publish only the allowlisted
        # performance settings, so no key, host or path from the server reaches the public repo.
        manifest["server_environment"] = {key: value for key, value in manifest["server_environment"].items()
                                          if recordable_setting(key)}
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


def published_suites():
    """The suites with published runs, current first."""
    found = set()
    for directory in sorted(PUBLISHED.iterdir() if PUBLISHED.exists() else []):
        found.add(suite_of(json.loads((directory / "manifest.json").read_text())))
    return [suite for suite in SUITES if suite in found]


def shown_suite():
    """The suite the README and the site lead with: the current one once it has published runs.

    Until then they keep showing the previous suite, so the switch never leaves them empty while
    the new suite's official runs are still being made."""
    return next(iter(published_suites()), CURRENT_SUITE)


def wilson(passed, n, z=1.96):
    """95% Wilson score interval for passed of n cases, in percent."""
    if not n:
        return None
    p = passed / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [100 * max(0.0, centre - half), 100 * min(1.0, centre + half)]


def entries(suite=None):
    """One entry per published run and model, for one suite (by default the one shown)."""
    suite = suite or shown_suite()
    tracks = tracks_of(suite)
    found = []
    for directory in sorted(PUBLISHED.iterdir() if PUBLISHED.exists() else []):
        manifest = json.loads((directory / "manifest.json").read_text())
        published = manifest.get("published", {})
        if suite_of(manifest) != suite or (
                manifest.get("status") not in ("complete", "complete_with_errors") and not published.get("rounds")):
            continue
        all_rows = read_rows(directory / "cases.jsonl")
        rows = [row for row in all_rows if row["phase"] == "warm"]
        repeats = [row for row in all_rows if row["phase"] == "repeat"]
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
            covered = coverage(mine, rounds, capabilities, tracks)
            first = {(row["case"], row["round"]): row["status"] for row in mine}
            again = [row for row in repeats if row["model"] == model and row["status"] != "unsupported"]
            planted = [row for row in mine if (manifest.get("case_metadata") or {}).get(row["case"], {}).get("injection")]
            supported_cases = sum(covered["track_cases"][track] for track in covered["supported_tracks"])
            passed = sum(row["status"] == "pass" and row["track"] in covered["supported_tracks"]
                         for row in mine) / rounds
            found.append({
                "run": directory.name, "suite": suite, "all_tracks": tracks, "device": device(manifest),
                "model": model,
                "thinking": manifest["options"]["thinking"],
                # Official runs record the hardware setup (--setup); everything else is exploratory.
                "setup": (manifest["options"].get("setup") or "").strip(),
                "threads": manifest["options"].get("threads"),
                "rounds": rounds,
                "score": sum(row["status"] == "pass" for row in mine) / rounds,
                "challenge": sum(row["status"] == "pass" and row["difficulty"] == "challenge"
                                 for row in mine) / rounds,
                "tiers": {tier: sum(row["status"] == "pass" and row["difficulty"] == tier for row in mine) / rounds
                          for tier in TIERS},
                "tier_cases": {tier: sum(row["difficulty"] == tier for row in mine) / rounds for tier in TIERS},
                # Suite 1.8 runs one pass, so it ranks by a 95% interval over the supported cases; suite
                # 1.7 ranks by the range across its passes, as it always has.
                "interval": wilson(passed, supported_cases) if suite != "1.7" else None,
                "tracks": {track: sum(row["status"] == "pass" and row["track"] == track for row in mine) / rounds
                           for track in tracks},
                # Planted-instruction cases: how many the model obeyed (averaged over passes).
                "injection": ({"cases": len(planted) / rounds,
                               "followed": sum(bool(row.get("injection_followed")) for row in planted) / rounds}
                              if planted else None),
                # Protocol 4's repeatability sample: how many repeated cases ended differently.
                "repeat": ({"cases": len(again),
                            "changed": sum(row["status"] != first.get((row["case"], row["round"])) for row in again)}
                           if again else None),
                "unsupported": sum(row["status"] == "unsupported" for row in rows if row["model"] == model),
                "capabilities": capabilities or [],
                **covered,
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


# Models that Ollama flags as thinking-capable (the flag comes from the chat template) but that
# never reason: with thinking on they write no thinking text, so run_problems refuses their
# thinking-on runs and none is published. Recorded here so the charts say why there is no
# thinking-on result, rather than implying one was simply not run.
NO_WORKING_THINKING = {"hf.co/LiquidAI/LFM2.5-1.2B-Instruct-GGUF:Q4_K_M"}


def primary_group(device_entries):
    """Split one device's entries into the main results and the other thread counts.

    The main results use PRIMARY_THREADS. An official run replaces exploratory runs of the same
    model and thinking mode. A model and mode with official results only at another thread count
    (gemma4:e4b, which hung the Pi and wasn't rerun) still appears, marked with threads_note,
    rather than silently disappearing. Shared by the README and the results site."""
    main = [entry for entry in device_entries if entry["threads"] == PRIMARY_THREADS]
    official = {(entry["model"], entry["thinking"]) for entry in main if entry["setup"]}
    group = [entry for entry in main if entry["setup"] or (entry["model"], entry["thinking"]) not in official]
    fallback = {}
    for entry in device_entries:
        key = (entry["model"], entry["thinking"])
        if entry["setup"] and entry["threads"] != PRIMARY_THREADS and key not in official:
            fallback.setdefault(key, entry)["threads_note"] = f"{entry['threads']} threads only"
    group += list(fallback.values())
    others = [entry for entry in device_entries if entry["threads"] != PRIMARY_THREADS and entry["setup"]
              and entry not in fallback.values()]
    return group, others


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


def supported_tracks(capabilities, tracks):
    return [track for track in tracks
            if track not in CAPABILITY_TRACKS or CAPABILITY_TRACKS[track] in capabilities]


def coverage(rows, rounds, capabilities=None, all_tracks=None):
    """Accuracy over the tracks a model supports, and over the text tracks every model has.

    A case the model can't attempt (no vision, no tools) is "not applicable", never a 0:
    it is left out of that model's accuracy, and the supported tracks are reported alongside.
    Which tracks are supported comes from the model's recorded capabilities, not from which
    rows happen to exist, so a run that left out a track can't pass for a missing capability."""
    all_tracks = all_tracks or tracks_of(CURRENT_SUITE)
    cases = {track: sum(row["track"] == track for row in rows) / rounds for track in all_tracks}
    passed = {track: sum(row["track"] == track and row["status"] == "pass" for row in rows) / rounds
              for track in all_tracks}
    if capabilities is None:  # older manifests: fall back to the tracks that have results
        tracks = [track for track in all_tracks if cases[track]]
    else:
        tracks = supported_tracks(capabilities, all_tracks)
        missing = [track for track in tracks if not cases[track]]
        if missing:
            raise ValueError(f"no results for supported track(s) {', '.join(missing)}")
    text = [track for track in tracks if track not in IMAGE_TRACKS]
    share = lambda chosen: (100 * sum(passed[t] for t in chosen) / sum(cases[t] for t in chosen)
                            if chosen and sum(cases[t] for t in chosen) else None)
    return {"supported_tracks": tracks, "track_cases": cases,
            "accuracy": share(tracks), "text_accuracy": share(text)}


def coverage_label(entry):
    missing = [TRACK_NAMES[track].lower() for track in entry["all_tracks"] if track not in entry["supported_tracks"]]
    label = f"{len(entry['supported_tracks'])}/{len(entry['all_tracks'])}"
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


def has_hard(totals):
    return bool(totals.get("hard"))


def table(group, totals):
    tracks = totals["tracks"]
    hard = has_hard(totals)
    # Short headers split over two lines (GitHub allows <br> in cells), so the columns stay narrow.
    head = ["Model", "Accuracy<br>(all)", "Accuracy<br>(text)", "Challenge", *(["Hard"] if hard else []),
            *(TRACK_NAMES[track] for track in tracks),
            *(["Planted<br>instructions<br>followed", "Repeats<br>changed"] if hard else []),
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
            (f"**{entry['accuracy']:.0f}%**" if len(entry["supported_tracks"]) == len(tracks)
             else coverage_label(entry).split("(")[-1].rstrip(")")), f"{entry['text_accuracy']:.0f}%",
            number(entry["challenge"]), *([number(entry["tiers"]["hard"])] if hard else []),
            *(number(entry["tracks"][track]) if track in entry["supported_tracks"] else "n/a" for track in tracks),
            *([of(entry["injection"], "followed"), of(entry["repeat"], "changed")] if hard else []),
            duration(entry["median_s"]), number(entry["timeouts"]), memory(entry), peak]) + " |")
    return "\n".join(lines)


def of(counts, key):
    """'2 of 10' from {"cases": 10, key: 2}, or a dash without counts."""
    return f"{number(counts[key])} of {number(counts['cases'])}" if counts else "—"


def interval_label(entry):
    low, high = entry["interval"]
    return f"{low:.0f}–{high:.0f}%"


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


def text_width(content, size):
    """Rough rendered width of a label in the system sans-serif, on the generous side."""
    narrow, wide = set("il.:,;!|'-·1()[]IJfjrt "), set("mwMW%@")
    units = sum(0.32 if ch in narrow else 0.86 if ch in wide else 0.68 if ch.isupper() else 0.6 for ch in content)
    return units * size


def svg_chart(points, theme, title):
    """Accuracy against speed. Bubble area shows peak memory in use, colour shows the thinking
    mode, and a thin connector joins each model's thinking-off and thinking-on points."""
    t = THEMES[theme]
    # The README shows the chart 920 px wide, so the plot gets the full width and the legend
    # sits in a row above it; both axes hug the data instead of starting at zero.
    width, height = 920, 820
    left, top, plot_w, plot_h = 68, 206, 824, 540
    font = 'font-family="system-ui, -apple-system, Segoe UI, sans-serif"'
    with_memory = [point for point in points if point["peak_mem_gb"]]
    # Area encodes memory: radius grows with the square root, 10 GB -> 32 px.
    scale = 32 / math.sqrt(10)

    def radius(point):
        return scale * math.sqrt(point["peak_mem_gb"]) if point["peak_mem_gb"] else 7

    def accuracy(point):
        # Over the tracks the model supports, as in the tables.
        return point["accuracy"]

    # Accuracy axis: round tens just beyond the data. Position, not length, carries the value,
    # so the axis needn't start at zero.
    scores = [accuracy(point) for point in points]
    y_low = max(0, 10 * math.floor((min(scores) - 9) / 10))
    y_high = min(100, 10 * math.ceil((max(scores) + 9) / 10))

    # Time axis (log): padded in pixels so the largest bubble and a label fit at either end.
    times = [point["median_s"] for point in points]
    pad = max(radius(point) for point in points) + 26
    span = math.log10(max(times)) - math.log10(min(times)) or 1
    per_decade = (plot_w - 2 * pad) / span
    x_low = math.log10(min(times)) - pad / per_decade
    x_high = math.log10(max(times)) + pad / per_decade
    fine = (1, 2, 3, 5, 10, 20, 30, 60, 120, 180, 300, 600, 900, 1200, 1800, 3600)
    ticks = [tick for tick in fine if x_low <= math.log10(tick) <= x_high]

    def x(seconds):
        return left + plot_w * (math.log10(seconds) - x_low) / (x_high - x_low)

    def y(percent):
        return top + plot_h * (y_high - percent) / (y_high - y_low)

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

    # Legend row: thinking modes, memory sizes, then the flag marker.
    ly, lx = 116, 24
    modes = [mode for mode in ("off", "on") if any(point["thinking"] == mode for point in points)]
    out.append(text(lx, ly + 5, "Thinking", size=14, fill="ink", weight="700"))
    lx += 76
    for mode in modes:
        out.append(f'<circle cx="{lx + 8}" cy="{ly}" r="8" fill="{t[mode]}"/>')
        out.append(text(lx + 22, ly + 5, mode, fill="ink"))
        lx += 62
    if with_memory:
        lx += 24
        out.append(text(lx, ly + 5, "Memory in use", size=14, fill="ink", weight="700"))
        lx += 112
        for gb in (2, 4, 8):
            r = scale * math.sqrt(gb)
            out.append(f'<circle cx="{lx + r:.1f}" cy="{ly}" r="{r:.1f}" fill="none" '
                       f'stroke="{t["ink2"]}" stroke-width="1.5"/>')
            out.append(text(lx + 2 * r + 8, ly + 5, f"{gb} GB", fill="ink"))
            lx += 2 * r + 58
    if any(point["flag"] for point in points):
        lx += 16
        out.append(f'<circle cx="{lx + 9}" cy="{ly}" r="9" fill="none" stroke="{t["ink2"]}" '
                   f'stroke-width="2" stroke-dasharray="4 3"/>')
        out.append(text(lx + 26, ly + 5, "Didn't reliably fit", fill="ink"))
    if with_memory:
        out.append(text(24, ly + 48, "For scale: the Pi has 8 GB of RAM and 2 GB of zram.", size=12.5))

    for percent in range(y_low, y_high + 1, 10):
        out.append(f'<line x1="{left}" x2="{left + plot_w}" y1="{y(percent):.1f}" y2="{y(percent):.1f}" '
                   f'stroke="{t["axis"] if percent == y_low else t["grid"]}" stroke-width="1"/>')
        out.append(text(left - 10, y(percent) + 4, f"{percent}%", size=12.5, anchor="end"))
    for tick in ticks:
        out.append(f'<line x1="{x(tick):.1f}" x2="{x(tick):.1f}" y1="{top}" y2="{top + plot_h}" '
                   f'stroke="{t["grid"]}" stroke-width="1"/>')
        label = f"{tick:g} s" if tick < 60 else f"{tick / 60:g} min"
        out.append(text(x(tick), top + plot_h + 20, label, size=12.5, anchor="middle"))
    out.append(text(left + plot_w / 2, top + plot_h + 50, "Median time per case (log scale)", size=14, fill="ink",
                    weight="700", anchor="middle"))
    out.append(text(left - 44, top - 16, "Accuracy (cases passed on supported tracks)", size=14, fill="ink", weight="700"))
    better = (left + plot_w - 8, top + 20)
    out.append(text(*better, "better ↖", size=13, anchor="end"))

    # Connectors first, so the bubbles sit on top of them.
    by_model = defaultdict(dict)
    for point in points:
        by_model[point["model"]][point["thinking"]] = point
    for modes_of in by_model.values():
        if "off" in modes_of and "on" in modes_of:
            a, b = modes_of["off"], modes_of["on"]
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

    def gap_to(box, cx, cy, cr):
        """Distance from a bubble's edge to a box; negative when they overlap."""
        x0, y0, x1, y1 = box
        nx, ny = min(max(cx, x0), x1), min(max(cy, y0), y1)
        return math.hypot(nx - cx, ny - cy) - cr

    def apart(a, b, margin=3):
        return a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1] or b[3] + margin <= a[1]

    # Label spots: 16 directions around each bubble, at growing distances (with a leader line
    # when pushed out). Every spot is scored; labels are placed greedily, crowded bubbles first,
    # then each is moved again, given where all the others ended up, until nothing improves.
    better_box = (better[0] - 70, better[1] - 14, better[0], better[1] + 4)
    specs = []
    for point, px, py, r in bubbles:
        label = f'{display_name(point["model"])} · {accuracy(point):.0f}%'
        # Flags and a different thread count go on a second line under the name, so the label stays narrow.
        notes = (["hung the Pi"] if point["flag"] else []) + ([point["threads_note"]] if point.get("threads_note") else [])
        note = f"({', '.join(notes)})" if notes else None
        w = max(text_width(label, 14.5), text_width(note or "", 12.5))
        h = 18 + (16 if note else 0)
        spots = []
        for distance in (5, 16, 30, 48, 72, 100):
            for k in range(16):
                angle = math.pi * k / 8
                c, s = math.cos(angle), math.sin(angle)
                ax, ay = px + (r + distance) * c, py + (r + distance) * s
                y0 = ay if s > 0.38 else ay - h if s < -0.38 else ay - h / 2
                # Beside the bubble reads best, then above, then below, then the diagonals.
                preference = 0 if k in (0, 8) else 2 if k == 12 else 3 if k == 4 else 6
                if c > 0.38:
                    starts = [ax]
                elif c < -0.38:
                    starts = [ax - w]
                else:
                    # Above or below: centred, or slid sideways so a bubble near the edge keeps its label.
                    starts = [ax - w / 2, ax - w + 14, ax - 14]
                for index, x0 in enumerate(starts):
                    spots.append(((x0, y0, x0 + w, y0 + h), distance + preference + 2 * bool(index)))
        specs.append({"point": point, "px": px, "py": py, "r": r, "label": label, "note": note, "spots": spots})

    def cost(spec, spot, others):
        box, base = spot
        x0, y0, x1, y1 = box
        outside = not (left + 2 <= x0 and x1 <= left + plot_w - 2 and top + 2 <= y0 and y1 <= top + plot_h - 2)
        circles = sum(gap_to(box, cx, cy, cr) < 3 for p, cx, cy, cr in bubbles if p is not spec["point"])
        labels = sum(not apart(box, other) for other in others) + (not apart(box, better_box))
        # A label nearer another bubble than its own reads as that bubble's label, leader or not.
        own = gap_to(box, spec["px"], spec["py"], spec["r"])
        misleading = sum(gap_to(box, cx, cy, cr) < own for p, cx, cy, cr in bubbles if p is not spec["point"])
        return 1000 * outside + 200 * circles + 200 * labels + 150 * misleading + base

    def crowding(spec):
        return sum(math.hypot(o["px"] - spec["px"], o["py"] - spec["py"]) < 140 for o in specs)

    chosen = {}
    for spec in sorted(specs, key=lambda item: (-crowding(item), item["label"])):
        others = [chosen[id(o)][0] for o in specs if id(o) in chosen]
        chosen[id(spec)] = min(spec["spots"], key=lambda spot: cost(spec, spot, others))
    for _ in range(6):
        moved = False
        for spec in specs:
            others = [chosen[id(o)][0] for o in specs if o is not spec]
            best = min(spec["spots"], key=lambda spot: cost(spec, spot, others))
            if cost(spec, best, others) < cost(spec, chosen[id(spec)], others):
                chosen[id(spec)], moved = best, True
        if not moved:
            break

    leaders, labels = [], []
    for spec in specs:
        (x0, y0, x1, y1), _ = chosen[id(spec)]
        px, py, r = spec["px"], spec["py"], spec["r"]
        # A label pushed away from its bubble gets a thin leader line, so it can't be mistaken for a neighbour's.
        nx, ny = min(max(px, x0), x1), min(max(py, y0), y1)
        gap = math.hypot(nx - px, ny - py) - r
        if gap > 12:
            ux, uy = (nx - px) / (gap + r), (ny - py) / (gap + r)
            leaders.append(f'<line x1="{px + ux * (r + 2):.1f}" y1="{py + uy * (r + 2):.1f}" x2="{nx - ux * 3:.1f}" '
                           f'y2="{ny - uy * 3:.1f}" stroke="{t["ink2"]}" stroke-width="1"/>')
        # Anchor the text on the side facing the bubble, so estimate errors fall away from it.
        if x1 <= px:
            tx, anchor = x1, "end"
        elif x0 >= px:
            tx, anchor = x0, "start"
        else:
            tx, anchor = (x0 + x1) / 2, "middle"
        labels.append(text(tx, y0 + 14, spec["label"], size=14.5, fill="ink", weight="600", anchor=anchor, halo=True))
        if spec["note"]:
            labels.append(text(tx, y0 + 30, spec["note"], size=12.5, fill="ink2", anchor=anchor, halo=True))
    out += leaders + labels
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


def glance_table(entries, total, intervals=False):
    lines = ["| Model | Thinking | Accuracy |" + (" 95% interval |" if intervals else "")
             + " Tracks | Time per case | Memory | Note |",
             "|---|---|---:|" + ("---:|" if intervals else "") + "---|---:|---|---|"]
    for entry in entries:
        notes = [entry["flag"]] if entry["flag"] else []
        if entry["timeouts"]:
            count = number(entry["timeouts"])
            # Averaged over passes, a count can be fractional; say it's per pass rather than "0.7 cases".
            per_pass = " per pass" if entry["rounds"] > 1 else ""
            notes.append(f"{count} case{'' if count == '1' else 's'}{per_pass} hit the {limit_label(entry)} limit")
        note = "; ".join(notes)
        lines.append(f"| `{display_name(entry['model'])}`{' (' + entry['threads_note'] + ')' if entry.get('threads_note') else ''} | {entry['thinking']} | **{entry['accuracy']:.0f}%** | "
                     + (f"{interval_label(entry)} | " if intervals else "")
                     + f"{coverage_label(entry)} | {duration(entry['median_s'])} | {short_memory(entry)} | {note} |")
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
            badges.append("no working thinking mode" if model in NO_WORKING_THINKING
                          else "thinking off only" if "thinking" in any_entry["capabilities"] else "no thinking mode")
        badges += [f"no {TRACK_NAMES[t].lower()}" for t in any_entry["all_tracks"]
                   if t not in any_entry["supported_tracks"]]
        rows.append({"model": display_name(model), "badges": badges,
                     **{mode: ({"accuracy": modes[mode]["accuracy"], "median_s": modes[mode]["median_s"]}
                               if mode in modes else None) for mode in ("off", "on")}})
    return sorted(rows, key=lambda row: -max(row[mode]["accuracy"] for mode in ("off", "on") if row[mode]))


def heatmap_rows(entries, totals):
    return [{"label": row_name(entry), "mode": entry["thinking"],
             "values": [100 * entry["tracks"][track] / entry["track_cases"][track]
                        if track in entry["supported_tracks"] else None for track in entry["all_tracks"]]
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


def suite_totals(suite):
    """Case counts for a suite: all, per tier and per track, plus the track order."""
    cases = suite_module(suite).dataset()
    tracks = tracks_of(suite)
    return {"all": len(cases), "tracks": tracks,
            **{tier: sum(case.difficulty == tier for case in cases) for tier in TIERS},
            **{track: sum(case.track == track for case in cases) for track in tracks}}


def tier_summary(totals):
    return ", ".join(f"{totals[tier]} {tier}" for tier in TIERS if totals[tier])


def build():
    suite = shown_suite()
    found = entries(suite)
    check_duplicates(found)
    totals = suite_totals(suite)
    version = suite_module(suite).VERSION
    parts = [START, "<!-- Generated: edit tools/publish.py and run `python3 tools/publish.py build`, not this section. -->", "",
             f"_Generated by `python3 tools/publish.py build` from the runs in "
             f"[`docs/results/`](docs/results/). Suite {version}: {totals['all']} cases "
             f"({tier_summary(totals)})._", ""]
    if suite != CURRENT_SUITE:
        current = suite_totals(CURRENT_SUITE)
        parts += [f"Suite {suite_module(CURRENT_SUITE).VERSION} ({current['all']} cases in {len(current['tracks'])} "
                  f"tracks) has replaced it, and its official runs on the Pi are in progress. Until they are "
                  f"published, these are the results of suite {version}. Scores from different suites can't be "
                  "compared.", ""]
    if not found:
        parts += ["No published runs for this suite version yet.", ""]
    by_device = defaultdict(list)
    for entry in found:
        by_device[entry["device"]].append(entry)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for name in sorted(by_device, key=lambda item: (not item.startswith("Raspberry"), item)):
        group, others = primary_group(by_device[name])
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
            parts += ["### At a glance", "", glance_table(official, total, has_hard(totals)), "",
                      "Accuracy is the share of cases passed on the tracks the model supports; Tracks shows how many "
                      f"of the {len(totals['tracks'])} that is. A track a model can't attempt (no vision, for example) "
                      "is not counted as 0%. "
                      + ("The 95% interval (Wilson) shows how far the accuracy could move by chance with this many "
                         "cases; results whose intervals overlap may not really differ. " if has_hard(totals) else "")
                      + "Time is the median per case. Memory is the peak in use, RAM plus zram, against the Pi's "
                      "7.9 GB of usable RAM.", ""]
            parts += picture(write_chart(f"thinking-{slug_name}", lambda theme: charts.dumbbell(
                dumbbell_rows(official, total), theme, "What thinking buys"),
                lambda theme: charts.dumbbell_mobile(dumbbell_rows(official, total), theme, "What thinking buys")),
                "Accuracy and median time per case for each model with thinking off and on")
            parts += ["### Strengths and weaknesses by track", ""]
            columns = [TRACK_NAMES[track] for track in totals["tracks"]] + ["All"]
            parts += picture(write_chart(f"tracks-{slug_name}", lambda theme: charts.heatmap(
                heatmap_rows(official, totals), columns, theme, "Where each model succeeds", totals["arithmetic"]),
                lambda theme: charts.heatmap_mobile(heatmap_rows(official, totals), columns, theme,
                                                    "Where each model succeeds")),
                "Share of each track's cases passed, per model and thinking mode")
        if pairs:
            low, high = pairs[0][0]["threads"], pairs[0][1]["threads"]
            parts += [f"### {low} vs {high} CPU threads", ""]
            thread_rows = [{"label": f"{display_name(a['model'])} · thinking {a['thinking']}",
                            "name": display_name(a["model"]), "mode": a["thinking"],
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
            for short, full in renamed:
                parts += [f"Note: Official filename for pulling `{short}` through Ollama is `{full}`.", ""]
        for mode in ("off", "on"):
            subset = [entry for entry in group if entry["thinking"] == mode]
            if subset:
                rounds = sorted({entry["rounds"] for entry in subset})
                note = (f"Challenge counts are out of {totals['challenge']} cases"
                        + (f", hard ones out of {totals['hard']}," if has_hard(totals) else "")
                        + f" and each track out of {totals['arithmetic']}.")
                if has_hard(totals):
                    note += (" Planted instructions followed counts the cases with an instruction hidden in a tool "
                             "result, a passage or a file that the model obeyed. Repeats changed counts the cases "
                             "of the fixed repeatability sample whose outcome differed when run again.")
                if any(len(entry["supported_tracks"]) < len(totals["tracks"]) for entry in subset):
                    note += (" A model without vision or tools has fewer challenge cases, and its unsupported "
                             "tracks show n/a.")
                if len(rounds) == 1 and rounds != [1]:
                    note = f"Scores are averaged over {rounds[0]} passes. " + note
                elif len(rounds) > 1:
                    # Mixed pass counts: name the models with more than one pass, the rest are single passes.
                    named = []
                    for count in [count for count in rounds if count > 1]:
                        models = sorted({f"`{display_name(entry['model'])}`" for entry in subset
                                         if entry["rounds"] == count})
                        named.append(f"{' and '.join([', '.join(models[:-1]), models[-1]] if len(models) > 1 else models)} "
                                     f"are averaged over {count} passes")
                    rest = "; the others come from a single pass" if 1 in rounds else ""
                    note = f"Scores for {'; '.join(named)}{rest}. " + note
                parts += [f"#### Thinking {mode}: scores by track", "", table(subset, totals), "", note, ""]
        if any(entry["peak_mem_gb"] is not None for entry in group):
            parts += [MEMORY_NOTE, ""]
        timed = [entry for entry in group if entry["thinking"] == "off"
                 and (entry["first_load_s"] is not None or entry["sustained_tok_s"])]
        if timed:
            parts += ["#### Speed (thinking off)", "", speed_table(timed), "", SPEED_NOTE.format(cases=totals["all"]), ""]
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
    add_command.add_argument("--rounds", help="publish only these complete rounds, e.g. 1-3; the manifest "
                                              "records which rounds the run had")
    add_command.add_argument("--note", help="a note shown with the results, e.g. why a run was cut short; "
                                            "needed with --allow-errors")
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
