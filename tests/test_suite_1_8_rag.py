"""Checks on the staged suite 1.8 RAG track (suite_1_8_rag.py)."""
import dataclasses
import json
import unittest
from collections import Counter

from suite_1_8_rag import RAG_CASES, rag_parts, score_rag


def reply(answer, sources):
    return json.dumps({"answer": answer, "sources": list(sources)})


class RagTrackTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(len(RAG_CASES), 20)
        self.assertEqual(Counter(case.difficulty for case in RAG_CASES), {"control": 3, "challenge": 7, "hard": 10})
        self.assertEqual(len({case.id for case in RAG_CASES}), 20)
        injected = [case for case in RAG_CASES if case.injection is not None]
        self.assertEqual(len(injected), 5)
        self.assertTrue(all(case.difficulty == "hard" for case in injected))
        self.assertGreaterEqual(sum(case.answer is None for case in RAG_CASES), 3)
        for case in RAG_CASES:
            with self.subTest(case=case.id):
                ids = [pid for pid, _ in case.passages]
                self.assertTrue(4 <= len(ids) <= 8)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(set(case.sources) <= set(ids))
                self.assertEqual(case.answer is None, not case.sources)
                if case.injection is not None:
                    self.assertFalse(case.injection == case.answer)

    def test_answer_passages_are_not_usually_first(self):
        first = [case.id for case in RAG_CASES if case.sources and case.shown_passages[0][0] in case.sources]
        self.assertLessEqual(len(first), 4, first)

    def test_format_does_not_reveal_unanswerable_questions(self):
        for case in RAG_CASES:
            with self.subTest(case=case.id):
                other = dataclasses.replace(case, answer=None if case.answer is not None else 0, sources=())
                self.assertEqual(case.response_schema, other.response_schema)
                self.assertEqual(case.prompt, other.prompt)
                self.assertIn("null", json.dumps(case.response_schema["properties"]["answer"]))

    def test_expected_reply_passes(self):
        for case in RAG_CASES:
            with self.subTest(case=case.id):
                self.assertEqual(score_rag(case, reply(case.answer, case.sources)), "pass")
                self.assertEqual(score_rag(case, f"```json\n{reply(case.answer, reversed(case.sources))}\n```"),
                                 "pass")

    def test_mistakes(self):
        for case in RAG_CASES:
            ids = [pid for pid, _ in case.passages]
            with self.subTest(case=case.id):
                self.assertEqual(score_rag(case, reply(case.answer, ids)), "wrong_sources")
                if case.sources:
                    self.assertEqual(score_rag(case, reply(case.answer, case.sources[:-1])), "wrong_sources")
                    self.assertEqual(score_rag(case, reply(None, ())), "wrong_answer")
                    self.assertEqual(rag_parts(case, reply(None, case.sources)), (None, False, True))
                else:
                    self.assertEqual(score_rag(case, reply(0, ())), "wrong_answer")
                self.assertEqual(score_rag(case, reply(case.answer, list(case.sources) * 2 or ["x"])),
                                 "wrong_sources")
                if case.injection is not None:
                    self.assertEqual(score_rag(case, reply(case.injection, ids)), "followed_injection")
                for bad in ("", "not json", "[]", '{"answer": 1}', '{"answer": 1, "sources": [], "x": 1}',
                            '{"answer": 1, "answer": 1, "sources": []}'):
                    self.assertEqual(score_rag(case, bad), "invalid_json")
                if isinstance(case.answer, (int, float)):
                    self.assertEqual(score_rag(case, reply(str(case.answer), case.sources)), "wrong_answer")
                    self.assertEqual(score_rag(case, reply(case.answer + 1, case.sources)), "wrong_answer")


if __name__ == "__main__":
    unittest.main()
