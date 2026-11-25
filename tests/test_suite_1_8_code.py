"""Checks on the staged suite 1.8 code track (suite_1_8_code.py): every case can be passed, every wrong
solution or mutant is caught, and the answers come from running trusted code, not from hand-typed values."""
import json
import time
import unittest
from collections import Counter

from code_runner import SandboxError, check_jail
from suite_1_8_code import CODE_CASES, printed, score_code

try:
    check_jail()
    JAIL = True
except SandboxError:
    JAIL = False


def fenced(source):
    return f"Here is the code.\n\n```python\n{source}```\n"


class CodeTrackShapeTest(unittest.TestCase):
    def test_counts_and_ids(self):
        self.assertEqual(len(CODE_CASES), 20)
        kinds = Counter(case.kind for case in CODE_CASES)
        self.assertEqual(kinds, {"generate": 6, "fix": 5, "predict": 4, "inverse": 1, "testgen": 4})
        ids = [case.id for case in CODE_CASES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(case.track == "code" and case.difficulty == "hard" for case in CODE_CASES))

    def test_wrong_solutions_and_mutants_exist_and_differ(self):
        for case in CODE_CASES:
            if case.kind in ("generate", "fix"):
                with self.subTest(case=case.id):
                    self.assertGreaterEqual(len(case.wrong), 2)
                    self.assertNotIn(case.reference, case.wrong)
                    self.assertEqual(len(set(case.wrong)), len(case.wrong), "a replace() that changed nothing")
            if case.kind == "testgen":
                with self.subTest(case=case.id):
                    self.assertGreaterEqual(len(case.wrong), 5)
                    self.assertEqual(len(set(case.wrong)), len(case.wrong))
                    self.assertNotIn(case.reference, case.wrong)

    def test_fix_prompts_show_the_buggy_code(self):
        for case in CODE_CASES:
            if case.kind == "fix":
                with self.subTest(case=case.id):
                    self.assertIn(case.wrong[0], case.prompt)

    def test_predicted_lines_come_from_running_the_program(self):
        for case in CODE_CASES:
            if case.kind == "predict":
                with self.subTest(case=case.id):
                    self.assertEqual(printed(case.program), case.answer)
                    self.assertIn(case.program, case.prompt)
                    self.assertNotIn(case.answer, case.prompt.replace(case.program, ""))

    def test_timed_tests_are_fast_for_the_reference(self):
        from suite_1_8_code import local
        for case in CODE_CASES:
            for test in case.tests:
                if len(test) == 4:
                    with self.subTest(case=case.id, test=test[0]):
                        start = time.perf_counter()
                        local(case.reference, test[1])
                        self.assertLess(time.perf_counter() - start, test[3] / 5, "too slow for the Pi")


@unittest.skipUnless(JAIL, "no OS sandbox here (Linux needs bubblewrap)")
class CodeTrackScoringTest(unittest.TestCase):
    def test_references_pass_and_wrong_solutions_fail(self):
        for case in CODE_CASES:
            if case.kind not in ("generate", "fix"):
                continue
            with self.subTest(case=case.id, solution="reference"):
                self.assertEqual(score_code(case, fenced(case.reference)), "pass")
            for index, wrong in enumerate(case.wrong):
                with self.subTest(case=case.id, solution=f"wrong {index + 1}"):
                    self.assertNotEqual(score_code(case, fenced(wrong)), "pass")

    def test_predictions(self):
        for case in CODE_CASES:
            if case.kind == "predict":
                with self.subTest(case=case.id):
                    self.assertEqual(score_code(case, json.dumps({"answer": case.answer})), "pass")
                    self.assertEqual(score_code(case, json.dumps({"answer": case.answer + " "})), "wrong_answer")
                    self.assertEqual(score_code(case, "not json"), "invalid_json")

    def test_inverse(self):
        case = next(case for case in CODE_CASES if case.kind == "inverse")
        self.assertEqual(score_code(case, json.dumps({"answer": case.sample})), "pass")
        for wrong in (505, 503, 17, 1504, "504", 5040):
            with self.subTest(wrong=wrong):
                self.assertEqual(score_code(case, json.dumps({"answer": wrong})), "wrong_answer")

    def test_reference_test_cases_catch_every_mutant(self):
        for case in CODE_CASES:
            if case.kind == "testgen":
                with self.subTest(case=case.id):
                    self.assertEqual(score_code(case, json.dumps({"tests": case.sample})), "pass")
                    weak = json.dumps({"tests": case.sample[:1]})
                    self.assertEqual(score_code(case, weak), "wrong_answer")
                    wrong = [dict(item, expected="not this") for item in case.sample]
                    self.assertEqual(score_code(case, json.dumps({"tests": wrong})), "wrong_answer")


if __name__ == "__main__":
    unittest.main()
