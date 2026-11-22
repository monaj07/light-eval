"""Tests for the publishing step (tools/publish.py) and the runner's failure handling.

Every number in the README comes from tools/publish.py, so it is tested here against copies of
a real published run, modified to show each way a run can go wrong."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cases import ROOT, TRACKS
from light_eval import infrastructure_errors, read_rows, thermal_band
from runtime import api_error, range_stats, server_environment

sys.path.insert(0, str(ROOT / "tools"))
import publish  # noqa: E402

SOURCE = ROOT / "docs" / "results" / "pi5-on-gemma4-e2b"


def load_run(path=SOURCE):
    return json.loads((path / "manifest.json").read_text()), read_rows(path / "cases.jsonl")


def exits(test, function, *args, **kwargs):
    """Run a function that should sys.exit, and return its message."""
    with test.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
        function(*args, **kwargs)
    return str(raised.exception.code)


class RunChecksTest(unittest.TestCase):
    def setUp(self):
        self.manifest, self.rows = load_run()

    def test_published_run_passes(self):
        self.assertEqual(publish.run_problems(self.manifest, self.rows), [])

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

    def test_incomplete_round_is_refused(self):
        rows = [row for row in self.rows if not (row["phase"] == "warm" and row["case"] == "math-units")]
        self.assertIn("round 1 of gemma4:e2b has 64 of 65 warm cases", publish.run_problems(self.manifest, rows))


class AddTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.run = self.tmp / "run"
        shutil.copytree(SOURCE, self.run)
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
        self.assertEqual(len(read_rows(self.tmp / "published" / "x" / "cases.jsonl")),
                         len(read_rows(SOURCE / "cases.jsonl")))

    def test_rounds_without_a_note(self):
        with contextlib.redirect_stdout(io.StringIO()):
            publish.add(self.run, "x", rounds="1")
        manifest = json.loads((self.tmp / "published" / "x" / "manifest.json").read_text())
        self.assertEqual(manifest["published"]["rounds"], [1])
        self.assertEqual(manifest["published"]["original_rounds"], [1])
        self.assertNotIn("note", manifest["published"])
        self.assertIsNone(publish.entries()[0]["note"])

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
        self.assertAlmostEqual(entry["accuracy"], 100 * entry["score"] / 65)
        # Two official runs of the same model, mode and threads: refuse rather than pick one.
        self.assertIn("Keep one", exits(self, publish.check_duplicates, found))


class CoverageTest(unittest.TestCase):
    def rows(self, tracks, status="pass"):
        return [{"track": track, "status": status} for track in tracks for _ in range(13)]

    def test_capabilities_decide_supported_tracks(self):
        rows = self.rows(["arithmetic", "structured", "agent", "context"])
        result = publish.coverage(rows, 1, ["completion", "tools"])
        self.assertEqual(result["supported_tracks"], ["arithmetic", "structured", "agent", "context"])
        self.assertEqual(result["accuracy"], 100)

    def test_missing_rows_for_a_supported_track_are_an_error(self):
        with self.assertRaisesRegex(ValueError, "structured, agent, vision, context"):
            publish.coverage(self.rows(["arithmetic"]), 1, ["completion", "tools", "vision"])

    def test_no_division_by_zero_without_text_tracks(self):
        result = publish.coverage(self.rows(["vision"]), 1)  # an old manifest without capabilities
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
                {"status": "api_error"}, {"status": "out_of_memory"}, {"status": "timeout"}]
        self.assertEqual(infrastructure_errors(rows), {"api_error": 2, "connection_error": 1})

    def test_server_environment_records_ollama_and_llama_settings(self):
        sources = ["/usr/bin/ollama serve PATH=/bin OLLAMA_FLASH_ATTENTION=1 LLAMA_ARG_CACHE_RAM=0 "
                   "OLLAMA_KV_CACHE_TYPE=q8_0 HOME=/root"]
        with patch("runtime._server_environment_sources", return_value=sources):
            self.assertEqual(server_environment(), {"LLAMA_ARG_CACHE_RAM": "0", "OLLAMA_FLASH_ATTENTION": "1",
                                                    "OLLAMA_KV_CACHE_TYPE": "q8_0"})
        with patch("runtime._server_environment_sources", return_value=[""]):
            self.assertIsNone(server_environment())


if __name__ == "__main__":
    unittest.main()
