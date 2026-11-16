#!/usr/bin/env python3
"""Benchmark local Ollama models in repeatable Raspberry Pi 5 workloads."""
import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
import platform
import random
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cases import ROOT, TOOLS, TRACKS, VERSION, Case, dataset, dispatch, parse_json, score
from runtime import BenchError, Ollama, Telemetry, read_text

PHASES = ("cold", "warm", "sustained")
COLD = Case("cold-request", "performance", 'Reply with only the JSON string "ready".', None)
WARMUP = Case("warmup", "performance", "Write a short sentence about a lighthouse.", None)
DECODE_PROMPT = (
    "Write a numbered list of at least sixty distinct short sentences about maintaining a garden. "
    "Begin with item 1 and continue the list as far as possible."
)


def model_name(name):
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def rounds(models, cycles, seed):
    """Balance positions per cycle and use opposite cyclic orders in each pair."""
    rng = random.Random(seed)
    for cycle in range(cycles):
        if cycle % 2 == 0:
            order = sorted(models)
            rng.shuffle(order)
        else:
            # Keep the first model and reverse the rest (ABC -> ACB).
            order = order[:1] + order[:0:-1]
        offsets = list(range(len(order)))
        rng.shuffle(offsets)
        for offset in offsets:
            yield order[offset:] + order[:offset]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def installed_models(api):
    return {model_name(item["name"]): item for item in api.json("tags")["models"]}


def inspect_models(api, models, requested_thinking):
    installed = installed_models(api)
    metadata = {}
    for name in models:
        if name not in installed or not installed[name].get("digest"):
            raise BenchError(f"Missing installed model {name}. Check ollama list and pull a local model.")
        info = api.json("show", {"model": name})
        if (name.endswith((":cloud", "-cloud")) or info.get("remote_host") or info.get("remote_model")
                or (info.get("model_info") or {}).get("general.remote_model")):
            raise BenchError(f"Cloud model {name} is outside this local benchmark.")
        capabilities = info.get("capabilities")
        if not isinstance(capabilities, list) or "completion" not in capabilities:
            raise BenchError(f"{name} needs the completion capability in /api/show. Use an up-to-date Ollama and a chat model.")
        thinking = info.get("thinking") or {}
        setting = None
        if "thinking" in capabilities and requested_thinking == "off":
            if "values" in thinking and not any(item is False for item in thinking["values"]):
                raise BenchError(f"{name} cannot disable thinking. Choose --thinking default for this run.")
            setting = False
        template = info.get("template", "")
        metadata[name] = {
            "digest": installed[name]["digest"], "size_bytes": installed[name].get("size"),
            "details": installed[name].get("details"), "capabilities": capabilities,
            "model_parameters": info.get("parameters"), "thinking_requested": setting,
            "thinking_metadata": thinking,
            "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
        }
    return metadata


def check_digest(api, model, expected):
    if installed_models(api).get(model, {}).get("digest") != expected:
        raise BenchError(f"Model {model} changed during the benchmark.")


def decode_rate(turn):
    metrics = turn.get("metrics", {})
    duration = metrics.get("eval_duration")
    tokens = metrics.get("eval_count")
    return tokens * 1e9 / duration if tokens and duration else None


def run_case(api, args, model, metadata, case, label, images, *, cap=None):
    options = dict(num_ctx=args.context, num_thread=args.threads, num_gpu=0, temperature=0,
                   num_predict=cap or args.max_tokens, seed=args.seed)
    if case.track == "agent" and "tools" not in metadata["capabilities"]:
        return {"status": "unsupported", "reason": "tools capability absent"}
    if case.track == "vision" and "vision" not in metadata["capabilities"]:
        return {"status": "unsupported", "reason": "vision capability absent"}
    # Distinct early text per measured request limits whole-prompt reuse. Keep the
    # reference identical across models, and across rounds for repeatability checks.
    reference = hashlib.sha256(label.encode()).hexdigest()[:12]
    user_message = {"role": "user", "content": f"Case reference {reference}.\n\n{case.prompt}"}
    if case.image:
        user_message["images"] = [images[case.image]]
    body = {"model": model, "messages": [user_message], "options": options,
            "keep_alive": "1h", "stream": True}
    if case.track == "agent":
        body["tools"] = TOOLS
    if metadata["thinking_requested"] is not None:
        body["think"] = metadata["thinking_requested"]
    trace, turns = [], []
    state = {"indicator": "red"}
    started = time.monotonic()
    deadline = started + args.timeout
    status, reason = "protocol_error", None
    try:
        for index in range(args.tool_rounds + 1 if case.track == "agent" else 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BenchError("Case time budget exhausted", "timeout")
            turn = {"content": "", "thinking": "", "tool_calls": [], "first_output_s": None,
                    "first_text_s": None}
            turns.append(turn)
            api.chat(body, remaining, turn)
            if turn["metrics"].get("done_reason") == "length" and case.track != "performance":
                status = "generation_limit"
                break
            calls = turn["tool_calls"]
            if not calls:
                status = "complete" if case.track == "performance" else score(
                    case, turn["content"], trace, state
                )
                break
            if case.track != "agent":
                raise BenchError("Unexpected tool call", "invalid_tool_call")
            if index == args.tool_rounds or len(trace) + len(calls) > args.tool_calls:
                raise BenchError("Tool call budget exhausted", "tool_limit")
            assistant = {"role": "assistant", "content": turn["content"], "tool_calls": calls}
            if turn["thinking"]:
                assistant["thinking"] = turn["thinking"]
            body["messages"].append(assistant)
            for call in calls:
                try:
                    name = call["function"]["name"]
                    values = call["function"]["arguments"]
                    if isinstance(values, str):
                        values = parse_json(values)
                    result = dispatch(name, values, state)
                except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
                    raise BenchError(f"Invalid tool call: {exc}", "invalid_tool_call") from exc
                trace.append({"name": name, "arguments": values, "result": result})
                body["messages"].append({"role": "tool", "tool_name": name,
                                         "content": json.dumps(result, sort_keys=True)})
    except BenchError as exc:
        status, reason = exc.status, str(exc)
    return {"status": status, "reason": reason, "wall_s": time.monotonic() - started,
            "ttft_s": turns[0]["first_output_s"] if turns else None,
            "first_text_s": turns[0]["first_text_s"] if turns else None,
            "generated_tokens": sum(turn.get("metrics", {}).get("eval_count", 0) for turn in turns),
            "turns": turns, "tool_trace": trace, "final_state": state}


def append_line(handle, record):
    handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
    handle.flush()


def run_slot(api, args, monitor, band, identity, candidates, images, records, blocks):
    model = identity["model"]
    metadata = identity["metadata"]
    header = {key: value for key, value in identity.items() if key != "metadata"}
    model_digest = metadata["digest"]
    if api.loaded():
        raise BenchError("Unexpected resident models before slot. Keep other Ollama clients idle.")
    check_digest(api, model, model_digest)
    print(f"Round {header['round']} position {header['position']}: {model}", flush=True)

    def measured(case, phase, label, cap=None, start=None):
        start = start or monitor.take()
        record = run_case(api, args, model, metadata, case, label, images, cap=cap)
        env = monitor.stats(start)
        if env["system_oom_kills"] and record["status"] in {"api_error", "connection_error", "timeout"}:
            record["status"] = "out_of_memory"
        row = {**header, "phase": phase, "case": case.id, "track": case.track,
               "environment": env, **record}
        append_line(records, row)
        return row

    def ensure_residency():
        running = api.loaded()
        if running and (len(running) != 1 or running[0].get("digest") != model_digest):
            raise BenchError("Unexpected resident model during slot.")
        return running[0] if running else None

    def prime():
        priming = run_case(api, args, model, metadata, WARMUP, "warmup", images, cap=16)
        resident = ensure_residency()
        if priming["status"] != "complete" or resident is None:
            if priming["status"] == "complete":
                priming["status"] = "protocol_error"
                priming["reason"] = "Model did not remain resident after warmup"
            return None, {key: priming.get(key) for key in ("status", "reason")}
        return resident, None

    def record_phase(phase, start, started, **extra):
        append_line(blocks, {**header, "phase": phase, "wall_s": time.monotonic() - started,
                             "environment": monitor.stats(start), **extra})

    try:
        if "cold" in args.phases:
            start = monitor.gate(band, args.cooldown)
            started = time.monotonic()
            measured(COLD, "cold", COLD.id, start=start)
            record_phase("cold", start, started)
            api.unload(model)
        if "warm" in args.phases:
            monitor.gate(band, args.cooldown)
            resident, failure = prime()
            start = monitor.gate(band, args.cooldown)
            started = time.monotonic()
            for case in candidates:
                unsupported = ((case.track == "agent" and "tools" not in metadata["capabilities"])
                               or (case.track == "vision" and "vision" not in metadata["capabilities"]))
                if failure and not unsupported:
                    append_line(records, {**header, "phase": "warm", "case": case.id, "track": case.track,
                                          "status": failure["status"], "reason": "Warmup failed: " + str(failure["reason"])})
                else:
                    case_start = None if unsupported else monitor.gate(band, args.cooldown)
                    measured(case, "warm", case.id, start=case_start)
                if not failure and not unsupported and ensure_residency() is None:
                    monitor.gate(band, args.cooldown)
                    resident, failure = prime()
            record_phase("warm", start, started, resident=resident, failure=failure)
            api.unload(model)
        if "sustained" in args.phases:
            monitor.gate(band, args.cooldown)
            resident, failure = prime()
            start = monitor.gate(band, args.cooldown)
            started = time.monotonic()
            deadline = started + args.sustain_seconds
            ordinal = 0
            if not failure:
                while time.monotonic() < deadline:
                    ordinal += 1
                    case = Case(f"decode-{ordinal:03d}", "performance", DECODE_PROMPT, None)
                    row = measured(case, "sustained", case.id, cap=args.sustain_tokens)
                    if row["status"] != "complete":
                        failure = {"status": row["status"], "reason": row.get("reason")}
                        break
            record_phase("sustained", start, started, resident=resident,
                         requests=ordinal, failure=failure, target_duration_s=args.sustain_seconds)
    finally:
        api.unload(model)


def spread(values):
    data = sorted(value for value in values if value is not None)
    if not data:
        return None
    return {"n": len(data), "median": statistics.median(data), "min": data[0], "max": data[-1],
            "p95": data[math.ceil(.95 * len(data)) - 1] if len(data) >= 20 else None}


def summarize(rows, blocks):
    groups, repeats = defaultdict(list), defaultdict(list)
    for row in rows:
        track = row["track"]
        if track == "context":
            track = "context-" + row["case"].split("-")[1]
        groups[row["model"], row["phase"], track].append(row)
        if row["phase"] == "warm" and row["status"] != "unsupported":
            repeats[row["model"], row["case"]].append(row)
    results = []
    for (model, phase, track), entries in sorted(groups.items()):
        statuses = Counter(row["status"] for row in entries)
        supported = len(entries) - statuses["unsupported"]
        finished = [row for row in entries if row.get("turns") and
                    all("metrics" in turn for turn in row["turns"]) and row["status"] != "timeout"]
        metrics = [turn["metrics"] for row in finished for turn in row["turns"]]

        def metric_spread(field, divisor=1):
            return spread(row[field] / divisor for row in metrics if field in row)

        results.append({
            "model": model, "phase": phase, "track": track, "n": len(entries),
            "outcomes": dict(statuses), "pass_rate": (statuses["pass"] / supported
                                                       if phase == "warm" and supported else None),
            "wall_s": spread(row.get("wall_s") for row in finished),
            "ttft_s": spread(row.get("ttft_s") for row in finished),
            "decode_tok_s": spread(decode_rate(turn) for row in finished for turn in row["turns"]),
            "generated_tokens": spread(row["generated_tokens"] for row in finished),
            "load_s": metric_spread("load_duration", 1e9),
            "prefill_s": metric_spread("prompt_eval_duration", 1e9),
            "prompt_tokens": metric_spread("prompt_eval_count"),
            "cached_prompt_tokens": metric_spread("prompt_eval_cached_count"),
            "cases_with_throttling_flags": sum(
                bool(row.get("environment", {}).get("active_throttling_bits_or")
                     or row.get("environment", {}).get("new_throttling_history_bits"))
                for row in entries
            ),
            "peak_system_used_estimate_kib": max(
                (row["environment"]["peak_system_used_estimate_kib"] for row in entries
                 if row.get("environment", {}).get("peak_system_used_estimate_kib") is not None), default=None
            ),
        })
    repeatability = {}
    for (model, _), entries in repeats.items():
        if len(entries) < 2:
            continue
        counters = repeatability.setdefault(model, {"repeated_cases": 0, "status_stable": 0,
                                                   "cases_with_complete_outputs": 0, "raw_output_stable": 0})
        counters["repeated_cases"] += 1
        counters["status_stable"] += len({row["status"] for row in entries}) == 1
        if all(row.get("turns") and all("metrics" in turn for turn in row["turns"]) for row in entries):
            counters["cases_with_complete_outputs"] += 1
            outputs = {json.dumps([{key: turn[key] for key in ("content", "thinking", "tool_calls")}
                                   for turn in row["turns"]], sort_keys=True) for row in entries}
            counters["raw_output_stable"] += len(outputs) == 1
    sustained = []
    for block in blocks:
        if block["phase"] != "sustained":
            continue
        decode = [row for row in groups[block["model"], "sustained", "performance"]
                  if row["round"] == block["round"] and row["status"] == "complete"]
        rates = [decode_rate(row["turns"][0]) for row in decode]
        span = max(1, len(rates) // 3)
        sustained.append({"model": block["model"], "round": block["round"],
                          "completed_requests": len(decode), "elapsed_s": block["wall_s"],
                          "target_duration_s": block["target_duration_s"], "failure": block["failure"],
                          "first_third_decode_tok_s": spread(rates[:span]) if len(rates) >= 3 else None,
                          "last_third_decode_tok_s": spread(rates[-span:]) if len(rates) >= 3 else None,
                          "environment": block["environment"]})
    return {"groups": results, "repeatability": repeatability, "sustained": sustained}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    temporary.replace(path)


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def print_summary(summary):
    for row in summary["groups"]:
        n = row["n"]
        status = row["outcomes"]
        latency = row["wall_s"]
        score_label = f", {status.get('pass', 0)}/{n - status.get('unsupported', 0)} passed" if row["pass_rate"] is not None else ""
        timing = f", median {latency['median']:.2f}s" if latency else ""
        print(f"{row['model']} {row['phase']}/{row['track']} ({n} cases{score_label}{timing}) {status}")
    for block in summary["sustained"]:
        if block["failure"]:
            print(f"{block['model']} sustained round {block['round']} stopped: {block['failure']['status']}")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Evaluate installed local models")
    run.add_argument("--models", nargs="+", required=True)
    run.add_argument("--port", type=int, default=11434, help="loopback Ollama port")
    run.add_argument("--out", type=Path, help="new output directory")
    run.add_argument("--tracks", choices=TRACKS, nargs="+", default=TRACKS)
    run.add_argument("--phases", choices=PHASES, nargs="+", default=PHASES)
    run.add_argument("--cycles", type=int, default=1, help="each cycle gives every model every run position")
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--setup", help="cooler, power supply, storage and Ollama service settings for the manifest")
    run.add_argument("--threads", type=int, default=2)
    run.add_argument("--context", type=int, default=4096)
    run.add_argument("--max-tokens", type=int, default=128)
    run.add_argument("--thinking", choices=("off", "default"), default="off")
    run.add_argument("--timeout", type=float, default=90, help="per-case deadline in seconds")
    run.add_argument("--tool-rounds", type=int, default=3)
    run.add_argument("--tool-calls", type=int, default=4)
    run.add_argument("--sustain-seconds", type=float, default=90)
    run.add_argument("--sustain-tokens", type=int, default=128)
    run.add_argument("--sample-seconds", type=float, default=1)
    run.add_argument("--cooldown", type=float, default=180, help="seconds to wait at a temperature gate")
    run.add_argument("--temp-min", type=float, help="override the idle-baseline-dependent minimum")
    run.add_argument("--temp-max", type=float, default=65, help="initial ceiling and upper bound on automatic band")
    run.add_argument("--no-thermal-gate", action="store_true", help="portable smoke test")
    run.add_argument("--plan", action="store_true", help="print schedule without connecting to Ollama")
    report = commands.add_parser("report", help="summarize a finished or partial run")
    report.add_argument("directory", type=Path)
    return parser


def run_benchmark(args, parser):
    for key in ("cycles", "threads", "context", "max_tokens", "timeout", "tool_rounds",
                "tool_calls", "sustain_seconds", "sustain_tokens", "sample_seconds", "cooldown"):
        value = getattr(args, key)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{key.replace('_', '-')} must be positive and finite")
    if not (0 <= args.seed <= 2**31 - 1 and 1 <= args.port <= 65535):
        parser.error("seed must be in [0, 2147483647] and port in [1, 65535]")
    if not math.isfinite(args.temp_max) or (args.temp_min is not None and
            (not math.isfinite(args.temp_min) or args.temp_min >= args.temp_max)):
        parser.error("temperature bounds must be finite and increasing")
    models = [model_name(name) for name in args.models]
    if len(set(models)) != len(models):
        parser.error("model names must be distinct")
    order = list(rounds(models, args.cycles, args.seed))
    selection = [case for case in dataset() if case.track in args.tracks]
    plan = []
    for round_num, model_order in enumerate(order, 1):
        cases = selection[:]
        random.Random(f"{args.seed}:cases:{round_num}").shuffle(cases)
        plan.append({"models": model_order, "cases": [case.id for case in cases]})
    if args.plan:
        print(json.dumps(plan, indent=2))
        return 0

    api = Ollama(args.port)
    lock_path = Path(tempfile.gettempdir()) / f"light-eval-{os.getuid()}-{args.port}.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BenchError("A Light Eval run already holds this Ollama port.") from exc
        return run_locked(args, api, models, plan, selection)


def run_locked(args, api, models, plan, selection):
    version = api.json("version").get("version")
    metadata = inspect_models(api, models, args.thinking)
    if api.loaded():
        raise BenchError("Ollama has resident models. Stop them with ollama ps / ollama stop MODEL before running.")
    timestamp = datetime.now(timezone.utc)
    out = args.out or Path("results") / timestamp.strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    images = {case.image: base64.b64encode((ROOT / "fixtures" / case.image).read_bytes()).decode()
              for case in selection if case.image}
    by_id = {case.id: case for case in selection}
    paths = [*sorted(ROOT.glob("*.py")), *sorted((ROOT / "fixtures").glob("*.png"))]
    manifest = {
        "suite_version": VERSION, "start_utc": timestamp.isoformat(), "status": "running",
        "ollama_version": version, "python": platform.python_version(), "platform": platform.platform(),
        "device_tree_model": read_text("/proc/device-tree/model"), "os_release": read_text("/etc/os-release"),
        "cpu_governor": read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        "memory_page_bytes": os.sysconf("SC_PAGE_SIZE"),
        "storage_page_cache": "OS page cache retained and uncontrolled",
        "warm_prompt_cache": "distinct early case references; Ollama reports cached tokens when available",
        "options": {key: value for key, value in vars(args).items() if key != "out"},
        "models": metadata, "plan": plan, "sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in paths},
    }
    write_json(out / "manifest.json", manifest)
    print(f"Writing {out}", flush=True)
    try:
        with (out / "cases.jsonl").open("w") as records, (out / "blocks.jsonl").open("w") as blocks, (
                out / "telemetry.jsonl").open("w") as telemetry, Telemetry(telemetry, args.sample_seconds) as monitor:
            band = None
            if not args.no_thermal_gate:
                if args.temp_min is None:
                    baseline = monitor.gate((0, args.temp_max), args.cooldown)["cpu_c"]
                    band = (max(0, baseline - 4), min(args.temp_max, baseline + 3))
                else:
                    band = (args.temp_min, args.temp_max)
            manifest["temperature_band_c"] = band
            write_json(out / "manifest.json", manifest)
            for round_num, row in enumerate(plan, 1):
                candidates = [by_id[key] for key in row["cases"]]
                for position, model in enumerate(row["models"], 1):
                    identity = {"round": round_num, "position": position, "model": model, "metadata": metadata[model]}
                    run_slot(api, args, monitor, band, identity, candidates, images, records, blocks)
        manifest["status"] = "complete"
    except (Exception, KeyboardInterrupt) as exc:
        manifest["status"] = "aborted"
        manifest["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        write_json(out / "manifest.json", manifest)
        summary = summarize(read_rows(out / "cases.jsonl"), read_rows(out / "blocks.jsonl"))
        write_json(out / "summary.json", summary)
    print_summary(summary)
    return 0


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "report":
            print_summary(summarize(read_rows(args.directory / "cases.jsonl"),
                                    read_rows(args.directory / "blocks.jsonl")))
            return 0
        return run_benchmark(args, parser)
    except (BenchError, OSError) as exc:
        print(f"light-eval: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
