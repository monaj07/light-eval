"""Tests for the results site's data (tools/build_site.py): it must say exactly what the README says."""
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from cases import ROOT
from light_eval import suite_module, suite_of

sys.path.insert(0, str(ROOT / "tools"))
import publish  # noqa: E402
import build_site as site_builder  # noqa: E402

# The suite the site leads with, from the published runs: its cases and tracks are what the site shows.
SHOWN = suite_module(publish.shown_suite())
TRACKS = SHOWN.TRACKS


def dataset():
    return SHOWN.dataset()


class SiteDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.data = site_builder.build_all(cls.tmp / "data")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_main_results_match_the_readme(self):
        found = publish.entries()
        pi = [entry for entry in found if entry["device"].startswith("Raspberry")]
        group, _ = publish.primary_group(pi)
        expected = {site_builder.entry_id(entry): entry for entry in group}
        primary = {entry["id"]: entry for entry in self.data["entries"] if entry["primary"]}
        self.assertEqual(set(primary), set(expected))
        for key, entry in primary.items():
            self.assertAlmostEqual(entry["accuracy"], expected[key]["accuracy"])
            self.assertAlmostEqual(entry["median_s"], expected[key]["median_s"])

    def test_pass_accuracies_average_to_the_accuracy(self):
        for entry in self.data["entries"]:
            with self.subTest(entry=entry["id"]):
                self.assertEqual(len(entry["pass_accuracy"]), entry["passes"])
                self.assertAlmostEqual(sum(entry["pass_accuracy"]) / entry["passes"], entry["accuracy"])

    def test_every_case_and_track_is_covered(self):
        ids = {case.id for case in dataset()}
        self.assertEqual({case["id"] for case in self.data["cases"]}, ids)
        for entry in self.data["entries"]:
            self.assertEqual(set(entry["case_pass"]), ids)
            self.assertEqual(set(entry["tracks"]), set(TRACKS))
            for track in TRACKS:
                # A track the model can't attempt is null, never 0%.
                self.assertEqual(entry["tracks"][track] is None, track not in entry["supported_tracks"])
            self.assertIn(entry["memory"], ("fits", "tight", "out"))

    def test_case_statuses_match_the_pass_fractions(self):
        for entry in self.data["entries"]:
            for case_id, statuses in entry["case_status"].items():
                with self.subTest(entry=entry["id"], case=case_id):
                    self.assertEqual(len(statuses), entry["passes"])
                    self.assertAlmostEqual(statuses.count("pass") / len(statuses), entry["case_pass"][case_id])

    def test_detail_files_and_images_exist(self):
        out = self.tmp / "data"
        for entry in self.data["entries"]:
            detail = json.loads((out / "models" / f"{entry['model_slug']}.json").read_text())
            self.assertIn(entry["id"], detail["entries"])
        for case in self.data["cases"]:
            if case["image"]:
                self.assertTrue((out / "images" / case["image"]).exists())
        prompts = json.loads((out / "cases.json").read_text())["prompts"]
        self.assertEqual(set(prompts), {case.id for case in dataset()})

    def test_data_is_valid_json_without_nan(self):
        # JavaScript's JSON.parse rejects NaN and Infinity, which Python writes by default.
        text = (self.tmp / "data" / "site.json").read_text()
        json.loads(text, parse_constant=lambda name: self.fail(f"{name} in site.json"))
        for entry in self.data["entries"]:
            for key, value in entry.items():
                if isinstance(value, float):
                    self.assertTrue(math.isfinite(value), f"{entry['id']}.{key}")

    def test_case_titles_drop_the_answer_format(self):
        self.assertEqual(site_builder.case_title('What is 173 × 29? Return only a JSON object with one key "answer".'),
                         "What is 173 × 29?")
        self.assertEqual(site_builder.case_title("Use the log to find X.\n\nlog line 1\nlog line 2"), "Use the log to find X.")



class ArchiveTest(unittest.TestCase):
    """Once suite 1.8 has published runs, the site leads with them and keeps suite 1.7 as an archive."""

    def test_current_suite_with_an_archive(self):
        from unittest.mock import patch
        from test_publish import suite_1_8_run, write_run
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        # The published suite 1.7 runs only, as they were before the switch.
        published = tmp / "published"
        published.mkdir()
        for run in publish.PUBLISHED.iterdir():
            if suite_of(json.loads((run / "manifest.json").read_text())) == "1.7":
                shutil.copytree(run, published / run.name)
        with patch.object(publish, "PUBLISHED", published):
            before = site_builder.build_all(tmp / "before")
        manifest, rows = suite_1_8_run()
        manifest["device_tree_model"] = "Raspberry Pi 5 Model B Rev 1.1"
        write_run(published / "pi5-on-gemma4-e2b-1.8", manifest, rows)
        with patch.object(publish, "PUBLISHED", published):
            data = site_builder.build_all(tmp / "data")
        self.assertEqual((data["suite"], data["protocol"], data["tiers"]), ("1.8.0", 4, ["control", "challenge", "hard"]))
        self.assertEqual(len(data["tracks"]), 8)
        self.assertEqual(data["archives"], [{"suite": "1.7", "version": "1.7.0", "path": "data/archive/1.7/"}])
        entry = data["entries"][0]
        self.assertEqual((entry["injection"], entry["repeat"]), ({"cases": 10, "followed": 0}, {"cases": 24, "changed": 0}))
        self.assertIsNotNone(entry["hard_accuracy"])
        self.assertTrue((tmp / "data" / "images" / "v1_8" / "photos" / "kangaroos.jpg").exists())
        archive = json.loads((tmp / "data" / "archive" / "1.7" / "site.json").read_text())
        self.assertEqual((archive["suite"], archive["current"]["version"], archive["archives"]), ("1.7.0", "1.8.0", []))
        # The archive holds exactly what the site showed for suite 1.7 before the switch.
        self.assertEqual([entry["id"] for entry in archive["entries"]], [entry["id"] for entry in before["entries"]])
        self.assertTrue(all(entry["interval"] is None for entry in archive["entries"]))
        self.assertIsNone(data["next"])
        self.assertEqual(before["next"], {"suite": "1.8", "version": "1.8.0", "cases": 160, "tracks": 8},
                         "before the switch, the site says suite 1.8 has replaced the suite it shows")
        self.assertEqual(before["archives"], [])


if __name__ == "__main__":
    unittest.main()
