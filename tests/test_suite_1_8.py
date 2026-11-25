"""Checks on the staged suite 1.8 cases (suite_1_8.py); the live suite is still 1.7."""
import json
import unittest
from collections import Counter
from dataclasses import replace

import sys
from collections import deque

from cases import ROOT, TRACKS, dataset, score
from light_eval import hashed_paths
from suite_1_8 import vision_cases
from test_benchmark import BenchmarkTest

FIXTURES = ROOT / "fixtures"
sys.path.insert(0, str(FIXTURES))
import suite_1_8_fixtures as data  # noqa: E402  (its constants import without Pillow)


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
        # Manifests hash light_eval.hashed_paths(), and publish.py checks the PNGs among them; staged
        # images must stay out of that set, or every suite 1.7 run would stop publishing.
        hashed = {path.resolve() for path in hashed_paths()}
        staged = {(FIXTURES / case.image).resolve() for case in vision_cases()}
        self.assertTrue(staged)
        self.assertFalse(hashed & staged)
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


class DrawingAnswersTest(unittest.TestCase):
    """Each drawn image's expected answer, recomputed from the constants that draw it."""
    def answer(self, case_id):
        return next(case.answer for case in vision_cases() if case.id == f"vision-{case_id}")

    def test_visitor_months(self):
        self.assertEqual(sum(b > a for a, b in zip(data.VISITORS_2025, data.VISITORS_2026)), self.answer("visitor-months"))
        self.assertTrue(all(abs(b - a) >= 5 for a, b in zip(data.VISITORS_2025, data.VISITORS_2026)))

    def test_next_train(self):
        now = data.CLOCK[0] * 60 + data.CLOCK[1]
        minutes = [int(t[:2]) * 60 + int(t[3:]) for t, place, _, status in data.DEPARTURES
                   if place == "Northport" and status == "On time"]
        self.assertEqual(min(m for m in minutes if m > now) - now, self.answer("next-train"))

    def test_promotion_items(self):
        paid = sum(float(price) - float(promo) for _, price, promo in data.RECEIPT if promo)
        self.assertAlmostEqual(paid, self.answer("promo-items"))
        self.assertAlmostEqual(sum(float(price) for _, price, _ in data.RECEIPT), 58.80)

    def test_floor_route(self):
        names = {name: (r, c) for r, row in enumerate(data.ROOMS) for c, name in enumerate(row)}
        links = {}
        for a, b in data.DOORS:
            links.setdefault(a, set()).add(b)
            links.setdefault(b, set()).add(a)
        start, goal = names["Entrance hall"], names["Study"]
        steps, queue = {start: 0}, deque([start])
        while queue:
            room = queue.popleft()
            for other in links.get(room, ()):
                if other not in steps:
                    steps[other] = steps[room] + 1
                    queue.append(other)
        self.assertEqual(steps[goal] - 1, self.answer("floor-route"))

    def test_net_sales_change(self):
        change = {name: (s2 - r2) - (s1 - r1) for name, s1, r1, s2, r2 in data.STORES}
        best = max(change, key=change.get)
        self.assertEqual(best, self.answer("net-sales-change"))
        self.assertEqual(sorted(change.values())[-1] - sorted(change.values())[-2], 2)  # a clear winner
        self.assertNotEqual(max(data.STORES, key=lambda row: row[3] - row[1])[0], best)  # sales alone mislead

    def test_heating_off(self):
        count = sum(data.HEATING_TARGET - t > 1.5 and not on for _, t, on in data.ROOM_TEMPERATURES)
        self.assertEqual(count, self.answer("heating-off"))

    def test_solar_share(self):
        share = {month: solar / (solar + grid + battery) for month, solar, grid, battery in data.ENERGY}
        self.assertEqual(max(share, key=share.get), self.answer("solar-share"))
        self.assertNotEqual(max(data.ENERGY, key=lambda row: row[1])[0], self.answer("solar-share"))

    def test_saturday_bus(self):
        park, central = data.STOPS.index("Park Street"), data.STOPS.index("Central Station")
        usable = [trip for trip in data.TRIPS if not trip[-1] and trip[park] != "—" and trip[park] >= "14:10"]
        self.assertEqual(min(usable, key=lambda trip: trip[park])[central], self.answer("saturday-bus"))

    def test_tracking_number(self):
        self.assertEqual(data.TRACKING_NUMBER, self.answer("tracking-number"))
        self.assertNotEqual(data.ORDER_NUMBER, data.TRACKING_NUMBER)


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
