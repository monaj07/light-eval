"""Checks on the staged suite 1.8 arithmetic, structured and context tracks (suite_1_8_text.py)."""
import json
import re
import unittest
from collections import Counter
from dataclasses import replace

from cases import score
from suite_1_8_text import ARITHMETIC_CASES, CONTEXT_CASES, STRUCTURED_CASES, context_cases
from test_benchmark import BenchmarkTest

TRACKS = {"arithmetic": ARITHMETIC_CASES, "structured": STRUCTURED_CASES, "context": CONTEXT_CASES}
ALTERNATIVE = {int: -987654, float: -98765.4321, str: "unseen-audit-value", dict: {"unseen": 1},
               list: ["unseen-audit-value"]}
CONTEXT_MAX_CHARS = 5844  # suite 1.7's longest context prompt


def answer_reply(case):
    return json.dumps(case.answer if case.track == "structured" else {"answer": case.answer})


class TextTracksTest(unittest.TestCase):
    def test_shape(self):
        for track, items in TRACKS.items():
            with self.subTest(track=track):
                self.assertEqual(len(items), 20)
                self.assertEqual(Counter(case.difficulty for case in items), {"control": 3, "challenge": 7, "hard": 10})
                self.assertTrue(all(case.track == track for case in items))
                self.assertEqual(len({case.id for case in items}), 20)

    def test_expected_answers_score_and_near_misses_fail(self):
        for items in TRACKS.values():
            for case in items:
                with self.subTest(case=case.id):
                    self.assertEqual(score(case, answer_reply(case), [], {}), "pass")
                    wrong = replace(case, answer=ALTERNATIVE[type(case.answer)])
                    self.assertEqual(score(wrong, answer_reply(case), [], {}), "wrong_answer")
                    if isinstance(case.answer, (int, float)) and not isinstance(case.answer, bool):
                        off = json.dumps({"answer": case.answer + 0.01})
                        self.assertEqual(score(case, off, [], {}), "wrong_answer")

    def test_money_answers_have_a_cent_tolerance_and_counts_none(self):
        for items in (ARITHMETIC_CASES, CONTEXT_CASES):
            for case in items:
                with self.subTest(case=case.id):
                    if isinstance(case.answer, float):
                        self.assertEqual(case.tolerance, 0.005)
                    else:
                        self.assertEqual(case.tolerance, 0.0)

    def test_structured_integers_are_strict(self):
        case = next(case for case in STRUCTURED_CASES if case.id == "json-crate-note")
        self.assertEqual(score(case, '{"id": "B-023", "units": 12.0, "fragile": true}', [], {}), "wrong_answer")

    def test_context_logs_are_deterministic_and_within_length(self):
        again = context_cases()
        for case, repeat in zip(CONTEXT_CASES, again):
            with self.subTest(case=case.id):
                self.assertEqual(case, repeat)
                self.assertLessEqual(len(case.prompt), CONTEXT_MAX_CHARS)
        hard = [len(case.prompt) for case in CONTEXT_CASES if case.difficulty == "hard"]
        self.assertGreater(sum(hard) / len(hard), 2500, "hard context cases should be long")

    def test_context_answers_are_not_trivially_in_the_question(self):
        for case in CONTEXT_CASES:
            question = case.prompt.rsplit("\n\n", 1)[-1]
            with self.subTest(case=case.id):
                if case.answer != "not found":
                    self.assertNotIn(str(case.answer), question)

    def test_absent_badge_really_is_absent(self):
        case = next(case for case in CONTEXT_CASES if case.id == "context-badge-absent")
        self.assertEqual(case.answer, "not found")
        self.assertFalse(re.search(r"badge 4471 entered server room", case.prompt))
        self.assertIn("badge 4471 denied at server room", case.prompt)
        self.assertIn("badge 4417 entered server room", case.prompt)

    def test_expenses_stay_in_june(self):
        case = next(case for case in CONTEXT_CASES if case.id == "context-house-expenses")
        self.assertEqual(set(re.findall(r"^2026-(\d\d)-", case.prompt, re.MULTILINE)), {"06"})


class TextTracksRequestTest(unittest.TestCase):
    """The runner's requests must not depend on the expected answers."""
    scripted_case = BenchmarkTest.scripted_case

    def test_requests_do_not_depend_on_expected_answers(self):
        for items in TRACKS.values():
            for case in items:
                with self.subTest(case=case.id):
                    reply = [{"content": answer_reply(case)}]
                    _, original = self.scripted_case(case, reply)
                    changed_case = replace(case, answer=ALTERNATIVE[type(case.answer)], tolerance=9999)
                    _, changed = self.scripted_case(changed_case, reply)
                    self.assertEqual([body for body, _ in original], [body for body, _ in changed])


del BenchmarkTest  # don't run the imported 1.7 tests twice

if __name__ == "__main__":
    unittest.main()
