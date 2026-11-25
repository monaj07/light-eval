"""Checks on the staged suite 1.8 instructions track (suite_1_8_instructions.py): every case can be passed,
and every checker rejects the mistake it exists for and nothing else."""
import json
import unittest
from collections import Counter

from suite_1_8_instructions import INSTRUCTION_CASES, failed_checks, score_instructions


# Single-fault edits of each case's sample: (case id, old text, new text, the checks the edit must break).
# Together with each case's own misses, they make every check reject at least one reply.
EDITS = [
    ("instr-sms-reminder", "10:30", "10.30", {"includes 10:30 and Dr Hale"}),
    ("instr-lowercase-caption", "lighthouse", "light", {"mentions the lighthouse"}),
    ("instr-alt-text", "Perth,", "Perth,\n", {"single line"}),
    ("instr-alt-text", "rising from", "rising, through autumn and early winter, steadily from", {"at most 125 characters"}),
    ("instr-shopping-list", "- cumin\n", "Shopping list:\n- cumin\n", {"only bullets"}),
    ("instr-out-of-office", "3 June", "June 3", {"includes 3 June"}),
    ("instr-out-of-office", "sam.ortiz@example.com", "sam@example.com", {"includes the address"}),
    ("instr-listing-json", '"Trail runner backpack"', "null", {"name is a string"}),
    ("instr-listing-json", '"capacity_litres": 12', '"capacity_litres": "12"', {"capacity 12"}),
    ("instr-listing-json", '["running", "hydration"]', '["hydration", "running"]', {"tags in order"}),
    ("instr-library-post", "Come and browse", "Come and browse our new reading rooms, join a family storytime session, "
     "use the free computers and wifi, or simply relax with a newspaper and a hot drink in the cafe. Come and browse",
     {"at most 280 characters"}),
    ("instr-study-table", "| Day | Task | Hours |", "| Day | Topic | Hours |", {"header row"}),
    ("instr-study-table", "| --- | --- | --- |", "| --- | --- |", {"separator row"}),
    ("instr-jacket-description", " The hood fits over a helmet and packs into the collar.", "",
     {"40 to 60 words"}),
    ("instr-jacket-description", "£89", "89 pounds", {"includes £89"}),
    ("instr-conventional-commit", "feat(parser): ", "feat: ", {"feat(parser) prefix"}),
    ("instr-conventional-commit", "- Split fields", "Split fields", {"two short body bullets"}),
    ("instr-inner-tube-json", "Replace a bicycle inner tube", "How to replace a bicycle inner tube quickly",
     {"title at most 6 words"}),
    ("instr-inner-tube-json", "Pull out the old inner tube", "Pull out the old inner tube and check the tyre",
     {"steps at most 8 words"}),
    ("instr-inner-tube-json", "Reconnect the brakes and test them", "Check the wheel is secure",
     {"warning mentions brakes"}),
    ("instr-no-digits", "Room Orchid", "the Orchid room", {"keeps the names"}),
    ("instr-no-digits", "printed copies.", "printed copies of the slides, and please arrive a few minutes early so "
     "we can start on time.", {"at most 30 words"}),
    ("instr-delay-texts", "We are sorry for the wait.", "We are sorry for the wait and will send another message "
     "as soon as we know more. Food vouchers are available at the information desk next to security.",
     {"at most 160 characters each"}),
    ("instr-reschedule-email", "Hi Jordan,", "Dear Jordan,", {"greeting"}),
    ("instr-reschedule-email", "Thanks,\nAna", "Best,\nAna", {"sign-off"}),
    ("instr-reschedule-email", "14:00", "2 pm", {"includes 14:00"}),
    ("instr-shoe-faq", "\nQ: Is there a warranty on the shoes?\nA: Every pair has a one-year warranty against faults "
     "in materials and stitching.", "", {"six lines", "topics in order"}),
    ("instr-shoe-faq", "A: You can", "You can", {"alternating Q and A"}),
    ("instr-shoe-faq", "How do returns work?", "How do returns work.", {"questions end with ?"}),
    ("instr-shoe-faq", "next working day.", "next working day!", {"no exclamation marks"}),
    ("instr-scale-recipe", "- 375 ml milk", "- 375 ml whole milk", {"line 2: - 375 ml milk"}),
    ("instr-scale-recipe", "- 45 g butter", "- 40 g butter", {"line 4: - 45 g butter"}),
    ("instr-scale-recipe", "- 450 g flour\n", "Ingredients for 6:\n- 450 g flour\n",
     {"only five bullets", "line 1: - 450 g flour", "line 2: - 375 ml milk", "line 3: - 0.75 tsp salt",
      "line 4: - 45 g butter", "line 5: - 2.25 tbsp sugar"}),
]


class InstructionTrackTest(unittest.TestCase):
    def test_counts_and_ids(self):
        self.assertEqual(len(INSTRUCTION_CASES), 20)
        self.assertEqual(Counter(case.difficulty for case in INSTRUCTION_CASES),
                         {"control": 3, "challenge": 7, "hard": 10})
        ids = [case.id for case in INSTRUCTION_CASES]
        self.assertEqual(len(ids), len(set(ids)))
        for case in INSTRUCTION_CASES:
            with self.subTest(case=case.id):
                names = [check.name for check in case.checks]
                self.assertEqual(len(names), len(set(names)))
                minimum = {"control": 1, "challenge": 3, "hard": 3}[case.difficulty]
                self.assertGreaterEqual(len(case.checks), minimum)

    def test_samples_pass(self):
        for case in INSTRUCTION_CASES:
            with self.subTest(case=case.id):
                self.assertEqual(failed_checks(case, case.sample), [])
                self.assertEqual(score_instructions(case, case.sample), "pass")
                # Surrounding whitespace never decides a case.
                self.assertEqual(score_instructions(case, f"\n  {case.sample}\n\n"), "pass")

    def test_each_miss_fails_exactly_its_checks(self):
        for case in INSTRUCTION_CASES:
            self.assertTrue(case.misses, case.id)
            for reply, expected in case.misses:
                with self.subTest(case=case.id, expected=sorted(expected)):
                    self.assertEqual(set(failed_checks(case, reply)), expected)
                    self.assertNotEqual(score_instructions(case, reply), "pass")
        cases = {case.id: case for case in INSTRUCTION_CASES}
        for case_id, old, new, expected in EDITS:
            case = cases[case_id]
            with self.subTest(case=case_id, edit=old[:30]):
                self.assertEqual(case.sample.count(old), 1, "the edit must change the sample exactly once")
                self.assertEqual(set(failed_checks(case, case.sample.replace(old, new))), expected)
        for case in INSTRUCTION_CASES:
            exercised = {name for _, names in case.misses for name in names}
            exercised |= {name for case_id, _, _, names in EDITS if case_id == case.id for name in names}
            with self.subTest(case=case.id):
                self.assertEqual([check.name for check in case.checks if check.name not in exercised], [],
                                 "every check must reject at least one reply")

    def test_garbage_fails_without_raising(self):
        nested = "[" * 3000 + "]" * 3000
        for case in INSTRUCTION_CASES:
            for reply in ("", "   ", "{}", "[]", "null", "- ", "---", nested, "\x00", "```json\n{}\n```"):
                with self.subTest(case=case.id, reply=reply[:12]):
                    self.assertNotEqual(score_instructions(case, reply), "pass")

    def test_json_cases(self):
        for case in INSTRUCTION_CASES:
            if case.json or case.sample.startswith("{"):
                with self.subTest(case=case.id):
                    self.assertTrue(case.json, "a JSON reply needs json=True")
                    self.assertEqual(score_instructions(case, "not json"), "invalid_json")
                    self.assertEqual(score_instructions(case, f"```json\n{case.sample}\n```"), "pass")
                    data = json.loads(case.sample)
                    duplicated = case.sample[:-1] + f', "{next(iter(data))}": 1}}'
                    self.assertEqual(score_instructions(case, duplicated), "invalid_json")


if __name__ == "__main__":
    unittest.main()
