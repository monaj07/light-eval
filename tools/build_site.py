#!/usr/bin/env python3
"""Build the results site's data (site/data/) from the published runs in docs/results/.

  python3 tools/build_site.py                       # writes site/data/
  python3 -m http.server -d site 8000         # preview at http://localhost:8000

Uses the same entries() and primary_group() as the README, so every number matches it.
Only site/data/ is generated; site/index.html, style.css, app.js and meta.json are
hand-written. Standard library only, like the runner. See docs/SITE.md.
"""
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from light_eval import CURRENT_SUITE, SUITE_PROTOCOLS, read_rows, suite_module  # noqa: E402
import publish  # noqa: E402

SITE = ROOT / "site"
EXCERPT = 280  # characters of each answer kept for the model pages


def case_title(prompt):
    """A case's prompt without the answer-format instruction and the data that follows it."""
    first = prompt.strip().split("\n", 1)[0]
    first = re.split(r"\s*Return only\b", first, maxsplit=1)[0].strip()
    return first if len(first) <= 150 else first[:147].rstrip() + "…"


def entry_id(entry):
    return f"{publish.slug(entry['model'])}-{entry['thinking']}-t{entry['threads']}"


def memory_status(entry):
    """The README's memory verdict: fits, tight or out (ran out of memory or hung the Pi)."""
    icon = publish.short_memory(entry)[:2]
    return {"✅": "fits", "⚠️": "tight", "❌": "out"}.get(icon.strip(), None)


def answer_excerpt(row):
    turns = row.get("turns") or []
    text = (turns[-1].get("content") or "").strip() if turns else ""
    if not text and row.get("tool_trace"):
        text = "tool calls: " + ", ".join(call.get("name", "?") for call in row["tool_trace"][:6])
    return text[:EXCERPT] + ("…" if len(text) > EXCERPT else "")


def thinking_chars(row):
    return sum(len(turn.get("thinking") or "") for turn in row.get("turns") or [])


def run_rows(run):
    return read_rows(publish.PUBLISHED / run / "cases.jsonl")


def expected(case):
    """What a case counts as correct, as shown on its page."""
    if case.track == "instructions":
        return "Rules checked: " + "; ".join(check.name for check in case.checks)
    if case.track == "rag":
        return json.dumps({"answer": case.answer, "sources": list(case.sources)}, ensure_ascii=False)
    if case.track == "code":
        if case.kind in ("predict", "inverse"):
            return json.dumps(case.answer, ensure_ascii=False)
        if case.kind == "testgen":
            return "Tests that pass on the correct function and fail on every planted bug"
        return f"Code that passes {len(case.tests)} hidden tests: " + ", ".join(test[0] for test in case.tests)
    return json.dumps(case.answer, ensure_ascii=False)


def expected_tools(case):
    if getattr(case, "reference", None) and case.track == "agent":  # suite 1.8: the reference solution
        return list(dict.fromkeys(name for name, _ in case.reference))
    return [name for name, _ in case.calls] if getattr(case, "calls", None) else []


def build_all(out=SITE / "data"):
    """The shown suite in site/data/, and every other suite with published runs as an archive in
    site/data/archive/<suite>/, which the site offers as the previous suite's results."""
    if out.exists():
        shutil.rmtree(out)
    shown = publish.shown_suite()
    archives = publish.archived_suites(shown)
    data = build(out, shown, archives)
    for suite in archives:
        build(out / "archive" / suite, suite, [], archive_of=shown)
    return data


def build(out, suite, archives=(), archive_of=None):
    found = publish.entries(suite)
    publish.check_duplicates(found)
    tracks = publish.tracks_of(suite)
    by_device = defaultdict(list)
    for entry in found:
        by_device[entry["device"]].append(entry)
    pi = next((name for name in by_device if name.startswith("Raspberry")), None)
    if pi is None:
        sys.exit("no published Raspberry Pi results")
    group, others = publish.primary_group(by_device[pi])
    primary_ids = {entry_id(entry) for entry in group}
    shown = group + [entry for entry in others if entry_id(entry) not in primary_ids]

    cases = suite_module(suite).dataset()
    by_case = {case.id: case for case in cases}
    manifests, rows_by_run = {}, {}
    for entry in shown:
        run = entry["run"]
        if run not in manifests:
            manifests[run] = json.loads((publish.PUBLISHED / run / "manifest.json").read_text())
            rows_by_run[run] = run_rows(run)

    if out.exists():
        shutil.rmtree(out)
    (out / "models").mkdir(parents=True)
    (out / "images").mkdir()

    site_entries, model_files = [], defaultdict(lambda: {"entries": {}})
    for entry in sorted(shown, key=lambda item: (-item["accuracy"], item["median_s"])):
        eid = entry_id(entry)
        manifest = manifests[entry["run"]]
        warm = [row for row in rows_by_run[entry["run"]] if row["model"] == entry["model"] and row["phase"] == "warm"]
        supported = [case.id for case in cases if case.track in entry["supported_tracks"]]
        passes = sorted({row["round"] for row in warm})
        # Accuracy in each pass, over the supported cases, as the README averages it.
        pass_accuracy = [100 * sum(row["status"] == "pass" for row in warm if row["round"] == number) / len(supported)
                         for number in passes]
        outcomes = defaultdict(list)
        for row in sorted(warm, key=lambda item: item["round"]):
            outcomes[row["case"]].append(row)
        case_pass = {case_id: (sum(row["status"] == "pass" for row in outcomes[case_id]) / len(outcomes[case_id])
                               if case_id in outcomes and case_id in supported else None)
                     for case_id in by_case}
        difficulty = {kind: [case_id for case_id in supported if by_case[case_id].difficulty == kind]
                      for kind in publish.TIERS}
        model_meta = manifest["models"].get(entry["model"], {})
        temperature = entry["temperature"] or {}
        site_entries.append({
            "id": eid, "model": entry["model"], "name": publish.display_name(entry["model"]),
            "model_slug": publish.slug(entry["model"]), "thinking": entry["thinking"],
            "threads": entry["threads"], "primary": eid in primary_ids, "official": bool(entry["setup"]),
            "threads_note": entry.get("threads_note"), "flag": entry["flag"], "note": entry["note"],
            "passes": len(passes), "accuracy": entry["accuracy"], "pass_accuracy": pass_accuracy,
            "interval": entry["interval"], "injection": entry["injection"], "repeat": entry["repeat"],
            "text_accuracy": entry["text_accuracy"],
            **{f"{kind}_accuracy": (100 * sum(case_pass[case_id] or 0 for case_id in ids) / len(ids) if ids else None)
               for kind, ids in difficulty.items()},
            "score": entry["score"], "supported_cases": len(supported),
            "tracks": {track: (100 * entry["tracks"][track] / entry["track_cases"][track]
                               if track in entry["supported_tracks"] else None) for track in tracks},
            "supported_tracks": entry["supported_tracks"], "capabilities": entry["capabilities"],
            "timeouts": entry["timeouts"], "timeout_s": entry["timeout_s"],
            "median_s": entry["median_s"], "read_s": entry.get("read_s"), "write_s": entry.get("write_s"),
            "prefill_tok_s": entry.get("prefill_tok_s"), "decode_tok_s": entry.get("case_decode_tok_s"),
            "sustained_tok_s": entry.get("sustained_tok_s"), "sustained_change": entry.get("sustained_change"),
            "first_load_s": entry.get("first_load_s"), "answer_tokens": entry.get("answer_tokens"),
            "peak_mem_gb": entry["peak_mem_gb"], "mem_exact": entry["mem_exact"], "memory": memory_status(entry),
            "min_free_gb": entry["min_available_kib"] / 2**20 if entry["min_available_kib"] is not None else None,
            "temp_mean": temperature.get("mean_cpu_c"), "temp_peak": temperature.get("peak_cpu_c"),
            "throttled_cases": temperature.get("throttled_cases"),
            "digest": (model_meta.get("digest") or "")[:12], "run": entry["run"], "case_pass": case_pass,
            # How each supported case ended in each pass (pass, wrong_answer, timeout, ...), in pass order.
            "case_status": {case_id: [row["status"] for row in outcomes[case_id]]
                            for case_id in by_case if case_id in outcomes and case_id in supported},
        })
        # Per-case detail for the model page, loaded only when that page opens.
        model_files[entry["model"]]["entries"][eid] = {
            case_id: [{"status": row["status"], "wall_s": row.get("wall_s"), "tokens": row.get("generated_tokens"),
                       "thinking_chars": thinking_chars(row), "answer": answer_excerpt(row),
                       "reason": (row.get("reason") or "")[:200] or None}
                      for row in outcomes[case_id]]
            for case_id in by_case if case_id in outcomes}

    # Paired tests (suite 1.8): which results each one is significantly ahead of, Holm-adjusted within
    # its thinking mode, for each leaderboard view (the main results, and the 2-thread runs), so a
    # result is only ever compared with the others in the view it appears in. None: not compared.
    views = {"main": group, "2": [entry for entry in shown if entry["threads"] == 2]}
    for site_entry in site_entries:
        site_entry["ahead"] = {}
    for view, together in views.items():
        ahead = publish.paired_ahead(together)
        index = {entry_id(entry): i for i, entry in enumerate(together)}
        for site_entry in site_entries:
            if site_entry["id"] in index:
                found = ahead[index[site_entry["id"]]]
                site_entry["ahead"][view] = None if found is None else sorted(entry_id(together[j]) for j in found)

    for model, data in model_files.items():
        data["model"] = model
        (out / "models" / f"{publish.slug(model)}.json").write_text(json.dumps(data, separators=(",", ":")))

    case_list = []
    for case in cases:
        if case.image:
            (out / "images" / case.image).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / "fixtures" / case.image, out / "images" / case.image)
        case_list.append({"id": case.id, "track": case.track, "difficulty": case.difficulty,
                          "title": case_title(case.prompt), "image": case.image,
                          "expected": expected(case), "tools": expected_tools(case),
                          "injection": bool(getattr(case, "has_injection", False))})
    (out / "cases.json").write_text(json.dumps(
        {"prompts": {case.id: case.prompt for case in cases}}, ensure_ascii=False, separators=(",", ":")))

    photos = []
    for path, alt in publish.HARDWARE_PHOTOS:
        source = ROOT / path
        if source.exists():
            shutil.copy(source, out / "images" / source.name)
            photos.append({"src": f"data/images/{source.name}", "alt": alt})
    setups = sorted({entry["setup"] for entry in group if entry["setup"]})
    updated = max((manifest.get("start_utc") or "") for manifest in manifests.values())
    data = {
        "suite": suite_module(suite).VERSION, "suite_key": suite, "protocol": SUITE_PROTOCOLS[suite],
        "updated": updated[:10],
        # The other suites' results, kept as archive views (data/archive/<suite>/site.json).
        "archives": [{"suite": key, "version": suite_module(key).VERSION, "path": f"data/archive/{key}/"}
                     for key in archives],
        # Set on an archive: the suite that replaced it.
        "current": ({"suite": archive_of, "version": suite_module(archive_of).VERSION}
                    if archive_of else None),
        # Set while an older suite is shown because the current one has no official Pi results yet.
        "next": ({"suite": CURRENT_SUITE, "version": suite_module(CURRENT_SUITE).VERSION,
                  "cases": len(suite_module(CURRENT_SUITE).dataset()),
                  "tracks": len(suite_module(CURRENT_SUITE).TRACKS)}
                 if suite != CURRENT_SUITE and not archive_of else None),
        "device": {"name": pi, "setup": setups[0] if setups else None, "photos": photos,
                   "ram_gb": 7.9, "zram_gb": 2.0},
        "primary_threads": publish.PRIMARY_THREADS,
        "tracks": [{"id": track, "name": publish.TRACK_NAMES[track],
                    "cases": sum(case.track == track for case in cases)} for track in tracks],
        "tiers": [tier for tier in publish.TIERS if any(case.difficulty == tier for case in cases)],
        "totals": {"cases": len(cases), **{tier: sum(case.difficulty == tier for case in cases)
                                           for tier in publish.TIERS}},
        "cases": case_list, "entries": site_entries,
    }
    (out / "site.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return data


if __name__ == "__main__":
    data = build_all()
    print(f"Wrote site/data/ with {len(data['entries'])} results and {len(data['cases'])} cases")
