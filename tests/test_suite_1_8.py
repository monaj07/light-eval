"""Checks on the staged suite 1.8 cases (suite_1_8.py); the live suite is still 1.7."""
import json
import unittest
from collections import Counter
from dataclasses import replace

from cases import ROOT, TRACKS, dataset, score
from suite_1_8 import vision_cases
from test_benchmark import BenchmarkTest

FIXTURES = ROOT / "fixtures"


def jpeg_markers(data):
    """The marker bytes of a JPEG's header segments, up to the start of the image data."""
    markers, index = [], 2
    while index + 4 <= len(data) and data[index] == 0xFF:
        marker = data[index + 1]
        markers.append(marker)
        if marker == 0xDA:  # start of scan: compressed data follows
            break
        index += 2 + int.from_bytes(data[index + 2:index + 4], "big")
    return markers


class SuiteVisionTest(unittest.TestCase):
    def test_vision_track_shape(self):
        cases = vision_cases()
        self.assertEqual(len(cases), 20)
        self.assertEqual(Counter(case.difficulty for case in cases), {"control": 3, "challenge": 7, "hard": 10})
        ids = [case.id for case in cases]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(set(ids) & {case.id for case in dataset()}, "1.8 ids must not reuse 1.7 ids")
        self.assertTrue(all(case.track == "vision" and case.track in TRACKS for case in cases))

    def test_images_exist_and_photos_carry_no_metadata(self):
        for case in vision_cases():
            with self.subTest(case=case.id):
                path = FIXTURES / case.image
                self.assertTrue(path.is_file(), case.image)
                self.assertTrue(case.image.startswith("v1_8/"))
                if path.suffix == ".jpg":
                    self.assertNotIn(0xE1, jpeg_markers(path.read_bytes()), "photos must carry no EXIF (GPS) segment")

    def test_live_suite_does_not_hash_staged_images(self):
        # The runner and publish.py hash fixtures/*.png; staged images must stay out of that set.
        self.assertFalse(any(path.parent != FIXTURES for path in FIXTURES.glob("*.png")))
        self.assertFalse(any(case.image and case.image.startswith("v1_8/") for case in dataset()))

    def test_expected_answers_score_and_alternatives_fail(self):
        for case in vision_cases():
            with self.subTest(case=case.id):
                self.assertEqual(score(case, json.dumps({"answer": case.answer}), [], {}), "pass")
                wrong = case.answer + ("x" if isinstance(case.answer, str) else 1)
                self.assertEqual(score(case, json.dumps({"answer": wrong}), [], {}), "wrong_answer")

    def test_multiple_choice_answers_are_listed_options(self):
        for case in vision_cases():
            if "only the letter of the correct option" in case.prompt:
                with self.subTest(case=case.id):
                    self.assertIn(f"{case.answer}) ", case.prompt)


class SuiteVisionRequestTest(unittest.TestCase):
    """The runner's requests for staged cases must not depend on the expected answers."""
    scripted_case = BenchmarkTest.scripted_case

    def test_requests_do_not_depend_on_expected_answers(self):
        for case in vision_cases():
            with self.subTest(case=case.id):
                reply = [{"content": json.dumps({"answer": case.answer})}]
                _, original = self.scripted_case(case, reply)
                alternative = {int: -987654, float: -98765.4321, str: "unseen-audit-value"}[type(case.answer)]
                _, changed = self.scripted_case(replace(case, answer=alternative, tolerance=9999), reply)
                self.assertEqual([body for body, _ in original], [body for body, _ in changed])


del BenchmarkTest  # don't run the imported 1.7 tests twice

if __name__ == "__main__":
    unittest.main()
