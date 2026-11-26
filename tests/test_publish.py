"""Tests for the publishing step (tools/publish.py) and the runner's failure handling.

Every number in the README comes from tools/publish.py, so it is tested here against copies of
a real published run, modified to show each way a run can go wrong."""
import contextlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import cases as suite_1_7
import suite_1_8
from cases import ROOT
from light_eval import infrastructure_errors, read_rows, thermal_band
from runtime import api_error, range_stats, server_environment

sys.path.insert(0, str(ROOT / "tools"))
import publish  # noqa: E402

# A published suite 1.7 run: the template for the suite 1.8 runs below, and the previous suite's
# results, which can no longer be published.
SOURCE = ROOT / "docs" / "results" / "pi5-on-gemma4-e2b"
TRACKS = suite_1_8.TRACKS
CASES = suite_1_8.dataset()


def load_run(path=SOURCE):
    return json.loads((path / "manifest.json").read_text()), read_rows(path / "cases.jsonl")


def suite_1_8_run():
    """An official suite 1.8 thinking-on run of gemma4:e2b that passes every check: the published
    suite 1.7 run's manifest and rows, reshaped to protocol 4 (every case once, then the repeatability
    sample), with the hashes of today's case files."""
    manifest, rows = load_run()
    template = next(row for row in rows if row["phase"] == "warm" and row["status"] == "pass")
    repeat = suite_1_8.repeat_sample(CASES)
    manifest.update(suite_version=suite_1_8.VERSION, protocol_version=4)
    manifest["options"].update(suite="1.8", tracks=list(TRACKS), passes=1, tool_rounds=12, tool_calls=16, threads=4)
    del manifest["options"]["cycles"]
    manifest["case_metadata"] = {case.id: {"track": case.track, "difficulty": case.difficulty,
                                           "injection": bool(getattr(case, "has_injection", False))} for case in CASES}
    manifest["plan"] = [{"models": ["gemma4:e2b"], "cases": [case.id for case in CASES],
                         "repeat": [case.id for case in repeat]}]
    manifest["sha256"] = {str(path.relative_to(ROOT)): publish.file_hash(path) for path in publish.case_files("1.8")}
    made = []
    for phase, chosen in (("warm", CASES), ("repeat", repeat)):
        for case in chosen:
            row = deepcopy(template)
            row.update(phase=phase, case=case.id, track=case.track, difficulty=case.difficulty)
            if getattr(case, "has_injection", False):
                row["injection_followed"] = False
            made.append(row)
    return manifest, made


def write_run(path, manifest, rows, blocks=()):
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps(manifest))
    (path / "cases.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (path / "blocks.jsonl").write_text("".join(json.dumps(block) + "\n" for block in blocks))


def exits(test, function, *args, **kwargs):
    """Run a function that should sys.exit, and return its message."""
    with test.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
        function(*args, **kwargs)
    return str(raised.exception.code)


class RunChecksTest(unittest.TestCase):
    def setUp(self):
        self.manifest, self.rows = suite_1_8_run()

    def test_published_run_passes(self):
        self.assertEqual(publish.run_problems(self.manifest, self.rows), [])

    def test_previous_suite_is_refused(self):
        manifest, rows = load_run()
        self.assertIn("suite 1.7.0, but the current suite is 1.8.0", publish.run_problems(manifest, rows))
        self.manifest["suite_version"] = "1.8.0-pilot"  # a pilot run, made before calibration
        self.assertIn("suite 1.8.0-pilot, but the current suite is 1.8.0",
                      publish.run_problems(self.manifest, self.rows))

    def test_other_protocol_is_refused(self):
        self.manifest["protocol_version"] = 3
        self.assertIn("protocol 3, but suite 1.8 runs use protocol 4", publish.run_problems(self.manifest, self.rows))

    def test_suite_1_8_budgets_are_the_standard(self):
        self.manifest["options"]["tool_rounds"] = 6  # suite 1.7's budget
        self.assertIn("--tool-rounds 6 instead of the standard 12", publish.run_problems(self.manifest, self.rows))

    def test_changed_suite_1_8_files_are_refused(self):
        for name in ("suite_1_8_agent.py", "code_runner.py", "fixtures/v1_8/photos/kangaroos.jpg"):
            with self.subTest(name=name):
                manifest = deepcopy(self.manifest)
                manifest["sha256"][name] = "0" * 64
                self.assertIn(f"{name} differs from the version this run used",
                              publish.run_problems(manifest, self.rows))

    def test_official_runs_need_the_repeatability_sample(self):
        rows = [row for row in self.rows if not (row["phase"] == "repeat" and row["track"] == "code")]
        self.assertIn("round 1 of gemma4:e2b repeated 21 of the 24 cases in the repeatability sample",
                      publish.run_problems(self.manifest, rows))
        self.manifest["options"]["setup"] = ""  # an exploratory run may leave it out
        self.assertEqual(publish.run_problems(self.manifest, rows), [])

    def test_track_subset_is_refused(self):
        self.manifest["options"]["tracks"] = ["arithmetic"]
        self.assertIn("ran only some tracks", " ".join(publish.run_problems(self.manifest, self.rows)))

    def test_nonstandard_budget_is_refused(self):
        self.manifest["options"]["context"] = 4096  # thinking on uses 10240
        self.assertIn("--context 4096 instead of the standard 10240",
                      publish.run_problems(self.manifest, self.rows))

    def test_prompt_cache_must_be_off(self):
        self.manifest["server_prompt_cache_ram_mib"] = "unset"
        self.assertIn("LLAMA_ARG_CACHE_RAM", " ".join(publish.run_problems(self.manifest, self.rows)))

    def test_changed_cases_are_refused(self):
        self.manifest["sha256"]["cases.py"] = "0" * 64
        self.assertIn("cases.py differs from the version this run used",
                      publish.run_problems(self.manifest, self.rows))

    def test_infrastructure_errors_are_refused_unless_allowed(self):
        rows = deepcopy(self.rows)
        rows[0]["status"] = "connection_error"
        self.assertIn("infrastructure reasons", " ".join(publish.run_problems(self.manifest, rows)))
        self.assertEqual(publish.run_problems(self.manifest, rows, allow_errors=True), [])

    def test_thinking_on_without_thinking_text_is_refused(self):
        rows = deepcopy(self.rows)
        for row in rows:
            for turn in row.get("turns") or []:
                turn["thinking"] = ""
        self.assertIn("gemma4:e2b produced thinking text in only 0 of 160 warm cases with answers",
                      " ".join(publish.run_problems(self.manifest, rows)))

    def test_failed_warmups_are_not_judged_for_thinking(self):
        # A run whose warmups all failed is refused for that, not misdiagnosed as having no thinking mode.
        rows = [{**row, "status": "out_of_memory", "turns": []} for row in self.rows]
        problems = " ".join(publish.run_problems(self.manifest, rows))
        self.assertNotIn("thinking mode", problems)

    def test_thinking_text_in_a_thinking_off_run_is_refused(self):
        manifest = deepcopy(self.manifest)
        manifest["options"].update(thinking="off", context=4096, max_tokens=1024,
                                   phases=["cold", "warm", "sustained"])
        self.assertIn("although thinking was off", " ".join(publish.run_problems(manifest, self.rows)))

    def test_run_without_warm_cases_is_refused(self):
        self.assertIn("gemma4:e2b has no warm cases", publish.run_problems(self.manifest, []))
        self.manifest["options"]["phases"] = ["cold"]
        self.assertIn("no warm phase", " ".join(publish.run_problems(self.manifest, [])))

    def test_official_runs_need_the_standard_phases(self):
        manifest = deepcopy(self.manifest)
        manifest["options"]["phases"] = ["warm", "sustained"]  # thinking on is warm only
        self.assertIn("thinking-on runs use warm", " ".join(publish.run_problems(manifest, self.rows)))
        manifest["options"]["setup"] = ""  # exploratory runs may differ, as long as they have warm
        self.assertEqual(publish.run_problems(manifest, self.rows), [])

    def test_sustain_and_seed_options_are_checked(self):
        self.manifest["options"].update(sustain_seconds=5, seed=7)
        problems = publish.run_problems(self.manifest, self.rows)
        self.assertIn("--sustain-seconds 5 instead of the standard 90", problems)
        self.assertIn("--seed 7 instead of the standard 42", problems)

    def test_failed_sustained_warmup_counts_as_an_infrastructure_error(self):
        blocks = [{"model": "gemma4:e2b", "phase": "sustained", "requests": 0,
                   "failure": {"status": "connection_error", "reason": "reset"}}]
        self.assertIn("gemma4:e2b: connection_error ×1",
                      " ".join(publish.run_problems(self.manifest, self.rows, blocks=blocks)))

    def test_incomplete_round_is_refused(self):
        rows = [row for row in self.rows if not (row["phase"] == "warm" and row["case"] == "rag-on-call")]
        self.assertIn("round 1 of gemma4:e2b has 159 of 160 warm cases", publish.run_problems(self.manifest, rows))


class AddTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.run = self.tmp / "run"
        write_run(self.run, *suite_1_8_run())
        patcher = patch.object(publish, "PUBLISHED", self.tmp / "published")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bad_flag_leaves_nothing_behind(self):
        self.assertIn("MODEL=TEXT", exits(self, publish.add, self.run, "x", flags=["no-equals-sign"]))
        self.assertIn("isn't in this run", exits(self, publish.add, self.run, "x", flags=["gemma4:e2bb=typo"]))
        self.assertFalse((self.tmp / "published" / "x").exists())

    def test_refused_run_leaves_nothing_behind(self):
        manifest = json.loads((self.run / "manifest.json").read_text())
        manifest["options"]["tracks"] = ["arithmetic"]
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        self.assertIn("can't be published", exits(self, publish.add, self.run, "x"))
        self.assertFalse((self.tmp / "published" / "x").exists())

    def test_truncated_last_line_is_skipped(self):
        with (self.run / "cases.jsonl").open("a") as handle:
            handle.write('{"model": "gemma4:e2b", "rou')  # a hang mid-write
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            publish.add(self.run, "x")
        self.assertEqual(len(read_rows(self.tmp / "published" / "x" / "cases.jsonl")), 160 + 24)

    def test_rounds_without_a_note(self):
        with contextlib.redirect_stdout(io.StringIO()):
            publish.add(self.run, "x", rounds="1")
        manifest = json.loads((self.tmp / "published" / "x" / "manifest.json").read_text())
        self.assertEqual(manifest["published"]["rounds"], [1])
        self.assertEqual(manifest["published"]["original_rounds"], [1])
        self.assertNotIn("note", manifest["published"])
        self.assertIsNone(publish.entries()[0]["note"])

    def test_published_manifest_keeps_only_allowlisted_server_settings(self):
        manifest = json.loads((self.run / "manifest.json").read_text())
        manifest["server_environment"] = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_API_KEY": "secret",
                                          "OLLAMA_HOST": "10.0.0.2", "LLAMA_ARG_API_KEY": "x",
                                          "LLAMA_ARG_CACHE_RAM": "0"}
        (self.run / "manifest.json").write_text(json.dumps(manifest))
        with contextlib.redirect_stdout(io.StringIO()):
            publish.add(self.run, "x")
        published = json.loads((self.tmp / "published" / "x" / "manifest.json").read_text())
        self.assertEqual(published["server_environment"], {"OLLAMA_FLASH_ATTENTION": "1", "LLAMA_ARG_CACHE_RAM": "0"})

    def test_entries_and_duplicates(self):
        with contextlib.redirect_stdout(io.StringIO()):
            publish.add(self.run, "a", flags=["gemma4:e2b=hung once"])
            publish.add(self.run, "b")
        found = publish.entries()
        self.assertEqual(len(found), 2)
        entry = found[0]
        self.assertEqual(entry["supported_tracks"], list(TRACKS))
        self.assertEqual(entry["flag"], "hung once")
        self.assertEqual(entry["timeout_s"], 600)
        self.assertAlmostEqual(entry["accuracy"], 100 * entry["score"] / 160)
        self.assertEqual(entry["tier_cases"], {"control": 21, "challenge": 49, "hard": 90})
        self.assertEqual(entry["injection"], {"cases": 10, "followed": 0})
        self.assertEqual(entry["repeat"], {"cases": 24, "changed": 0})
        self.assertEqual([round(bound, 1) for bound in entry["interval"]], [97.7, 100.0])
        # Two official runs of the same model, mode and threads: refuse rather than pick one.
        self.assertIn("Keep one", exits(self, publish.check_duplicates, found))

    def test_repeats_that_changed_and_planted_instructions_followed(self):
        manifest, rows = suite_1_8_run()
        for row in rows:
            if row["phase"] == "repeat" and row["track"] == "rag":
                row["status"] = "wrong_answer"
            if row["phase"] == "warm" and row["case"] == "agent-frost-injection":
                row.update(status="followed_injection", injection_followed=True)
        write_run(self.tmp / "published" / "a", manifest, rows)
        entry = publish.entries("1.8")[0]
        self.assertEqual(entry["repeat"], {"cases": 24, "changed": 3})
        self.assertEqual(entry["injection"], {"cases": 10, "followed": 1})

    def test_shown_suite_waits_for_the_current_suite(self):
        # Only suite 1.7 results: the README and site keep showing them, with no gap at the switch.
        shutil.copytree(SOURCE, self.tmp / "published" / "old")
        self.assertEqual((publish.published_suites(), publish.shown_suite()), (["1.7"], "1.7"))
        self.assertEqual(publish.entries()[0]["all_tracks"], suite_1_7.TRACKS)
        self.assertIsNone(publish.entries()[0]["interval"], "suite 1.7 keeps its ranges across passes")
        with contextlib.redirect_stdout(io.StringIO()):
            publish.add(self.run, "new")
        self.assertEqual((publish.published_suites(), publish.shown_suite()), (["1.8", "1.7"], "1.8"))
        self.assertEqual([entry["run"] for entry in publish.entries()], ["new"])
        self.assertEqual([entry["run"] for entry in publish.entries("1.7")], ["old"])


class CoverageTest(unittest.TestCase):
    def rows(self, tracks, status="pass"):
        return [{"track": track, "status": status} for track in tracks for _ in range(13)]

    def test_wilson_interval(self):
        low, high = publish.wilson(107, 160)
        self.assertEqual((round(low, 1), round(high, 1)), (59.3, 73.7))
        self.assertEqual(publish.wilson(0, 20)[0], 0)
        self.assertIsNone(publish.wilson(0, 0))

    def test_capabilities_decide_supported_tracks(self):
        rows = self.rows(["arithmetic", "structured", "agent", "context"])
        result = publish.coverage(rows, 1, ["completion", "tools"], suite_1_7.TRACKS)
        self.assertEqual(result["supported_tracks"], ["arithmetic", "structured", "agent", "context"])
        self.assertEqual(result["accuracy"], 100)

    def test_missing_rows_for_a_supported_track_are_an_error(self):
        with self.assertRaisesRegex(ValueError, "structured, agent, vision, context"):
            publish.coverage(self.rows(["arithmetic"]), 1, ["completion", "tools", "vision"], suite_1_7.TRACKS)

    def test_no_division_by_zero_without_text_tracks(self):
        result = publish.coverage(self.rows(["vision"]), 1, None, suite_1_7.TRACKS)  # an old manifest without capabilities
        self.assertIsNone(result["text_accuracy"])
        self.assertEqual(result["accuracy"], 100)

    def test_temperatures_are_time_weighted(self):
        items = [{"model": "m", "mean_cpu_c": 50, "cases": 10, "samples": 100, "peak_cpu_c": 55, "throttled_cases": 0},
                 {"model": "m", "mean_cpu_c": 70, "cases": 10, "samples": 300, "peak_cpu_c": 75, "throttled_cases": 0}]
        self.assertEqual(publish.combined_temperatures(items)["m"]["mean_cpu_c"], 65)

    def test_limit_label_follows_the_timeout(self):
        self.assertEqual(publish.limit_label({"timeout_s": 600}), "10-minute")
        self.assertEqual(publish.limit_label({"timeout_s": 900}), "15-minute")


class RunnerFailureTest(unittest.TestCase):
    def test_peak_memory_with_disk_swap(self):
        low = {"MemTotal": 8_000_000, "MemAvailable": 3_000_000, "SwapUsed": 0}
        high = {"MemTotal": 8_000_000, "MemAvailable": 200_000, "SwapUsed": 1_500_000}
        # Without a zram figure the high sample can't be computed: unknown, never the lower peak.
        self.assertIsNone(range_stats([low, high])["peak_memory_in_use_kib"])
        # New samples always record zram's RAM, 0 without zram, so disk swap counts in full.
        stats = range_stats([{**low, "zram_ram_kib": 0}, {**high, "zram_ram_kib": 0}])
        self.assertEqual(stats["peak_memory_in_use_kib"], 9_300_000)

    def test_model_tool_call_parse_errors_are_the_models_failure(self):
        self.assertEqual(api_error("XML syntax error on line 10: unexpected EOF", 500).status, "invalid_tool_call")
        self.assertEqual(api_error("model requires more system memory", 500).status, "out_of_memory")
        self.assertEqual(api_error("model runner has unexpectedly stopped", 500).status, "api_error")

    def test_thermal_band_is_never_empty(self):
        self.assertEqual(thermal_band(45.0, 50), (41.0, 48.0))
        low, high = thermal_band(72.0, 65)  # the baseline gate timed out hot
        self.assertEqual((low, high), (58, 65))

    def test_truncated_last_line(self):
        path = Path(tempfile.mkdtemp()) / "rows.jsonl"
        self.addCleanup(shutil.rmtree, path.parent)
        path.write_text('{"a": 1}\n{"a": 2}\n{"a"')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(read_rows(path), [{"a": 1}, {"a": 2}])
        path.write_text('{"a": 1}\n{"a"\n{"a": 2}\n')
        with self.assertRaises(json.JSONDecodeError):
            read_rows(path)

    def test_infrastructure_errors_are_counted(self):
        rows = [{"status": "pass"}, {"status": "api_error"}, {"status": "connection_error"},
                {"status": "api_error"}, {"status": "out_of_memory"}, {"status": "timeout"},
                {"status": "context_overflow"}]
        self.assertEqual(infrastructure_errors(rows), {"api_error": 2, "connection_error": 1})
        # A failed sustained warmup writes only a block; a failure after some requests has rows too.
        blocks = [{"phase": "sustained", "requests": 0, "failure": {"status": "api_error"}},
                  {"phase": "sustained", "requests": 4, "failure": {"status": "api_error"}},
                  {"phase": "warm", "failure": {"status": "connection_error"}}]
        self.assertEqual(infrastructure_errors(rows, blocks), {"api_error": 3, "connection_error": 1})

    def test_context_overflow_is_the_models_result(self):
        error = api_error("request (612 tokens) exceeds the available context size (256 tokens), "
                          "try increasing it", 400)  # Ollama 0.40.17
        self.assertEqual(error.status, "context_overflow")

    def test_server_environment_records_ollama_and_llama_settings(self):
        sources = ["/usr/bin/ollama serve PATH=/bin OLLAMA_FLASH_ATTENTION=1 LLAMA_ARG_CACHE_RAM=0 "
                   "OLLAMA_KV_CACHE_TYPE=q8_0 HOME=/root OLLAMA_API_KEY=secret OLLAMA_HOST=0.0.0.0 "
                   "OLLAMA_MODELS=/home/someone/models LLAMA_ARG_API_KEY=x"]
        with patch("runtime._server_environment_sources", return_value=sources):
            self.assertEqual(server_environment(), {"LLAMA_ARG_CACHE_RAM": "0", "OLLAMA_FLASH_ATTENTION": "1",
                                                    "OLLAMA_KV_CACHE_TYPE": "q8_0"})
        with patch("runtime._server_environment_sources", return_value=[""]):
            self.assertIsNone(server_environment())


class BubbleChartTest(unittest.TestCase):
    """The accuracy-and-speed chart's layout rules, checked on the published chart and on a crowded
    synthetic one, so a new model can't silently break them: every label sits inside the plot,
    clear of every other label and every other bubble, and nearer its own bubble than any other."""
    LEFT, TOP, RIGHT, BOTTOM = 68, 206, 68 + 824, 206 + 540

    def layout(self, svg):
        bubbles = [tuple(map(float, found)) for found in
                   re.findall(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)" fill="[^"]+" fill-opacity', svg)]
        boxes = []
        for x, y, anchor, content in re.findall(
                r'<text x="([\d.]+)" y="([\d.]+)" font-size="14.5" font-weight="600" text-anchor="(\w+)"[^>]*>([^<]+)<', svg):
            x, y, w = float(x), float(y), publish.text_width(content, 14.5)
            x0 = x if anchor == "start" else x - w if anchor == "end" else x - w / 2
            boxes.append((content, (x0, y - 14, x0 + w, y + 4)))
        return bubbles, boxes

    def gap(self, box, cx, cy, r):
        x0, y0, x1, y1 = box
        return math.hypot(min(max(cx, x0), x1) - cx, min(max(cy, y0), y1) - cy) - r

    def check(self, svg, count):
        bubbles, boxes = self.layout(svg)
        self.assertEqual((len(bubbles), len(boxes)), (count, count))
        for index, (name, box) in enumerate(boxes):
            x0, y0, x1, y1 = box
            self.assertTrue(self.LEFT <= x0 and x1 <= self.RIGHT and self.TOP <= y0 and y1 <= self.BOTTOM,
                            f"{name} leaves the plot")
            for other_name, other in boxes[index + 1:]:
                apart = x1 <= other[0] or other[2] <= x0 or y1 <= other[1] or other[3] <= y0
                self.assertTrue(apart, f"{name} overlaps {other_name}")
            gaps = sorted(self.gap(box, *bubble) for bubble in bubbles)
            # Its own bubble is the nearest; no other bubble is touched.
            self.assertGreaterEqual(gaps[1], 0, f"{name} covers another bubble")
            self.assertLess(gaps[0], gaps[1], f"{name} is nearer another bubble than its own")

    def test_published_chart(self):
        svg = (ROOT / "docs" / "assets" / "score-vs-time-raspberry-pi-5-8-gb-light.svg").read_text()
        self.check(svg, svg.count('fill-opacity="0.9"'))

    def test_crowded_chart(self):
        # A tight cluster of similar models, as a batch of new small models would add.
        points = [{"model": f"model-{i}:{size}b", "thinking": "on" if i % 3 == 0 else "off",
                   "median_s": 8 * 1.18 ** i, "accuracy": 55 + 4 * math.sin(i), "peak_mem_gb": 1.5 + i % 4,
                   "flag": None} for i, size in enumerate((1, 2, 3, 4, 7, 8, 9, 12, 14, 27))]
        self.check(publish.svg_chart(points, "light", "Crowded"), len(points))


if __name__ == "__main__":
    unittest.main()
