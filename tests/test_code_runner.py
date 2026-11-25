"""The code runner for the suite 1.8 code track: statuses, both safety layers, and mutation testing."""
import sys
import unittest

from code_runner import MARK, Result, check_source, extract_code, run_jobs, run_mutation, run_tests

ADD = "def add(a, b):\n    return a + b\n"


def raw(source, tests=(("t", "pass"),)):
    """Run source in the child without the syntax-tree check, to test the runtime layer on its own."""
    return run_jobs([{"source": source, "tests": [list(t) for t in tests], "requires": [], "discover": False}])[0]


class ExtractTest(unittest.TestCase):
    def test_prefers_a_python_block(self):
        text = "Here:\n```text\nnot code\n```\n```python\nx = 1\n```\n```python\nx = 2\n```"
        self.assertEqual(extract_code(text), "x = 1\n")
        self.assertEqual(extract_code("```py\ny = 2\n```"), "y = 2\n")

    def test_falls_back_to_any_block_then_the_whole_answer(self):
        self.assertEqual(extract_code("```\nz = 3\n```"), "z = 3\n")
        self.assertEqual(extract_code("z = 3"), "z = 3")
        self.assertEqual(extract_code("```sql\nSELECT 1;\n```", "sql"), "SELECT 1;\n")


class CheckSourceTest(unittest.TestCase):
    def test_rejects_escapes(self):
        for source in ("import os", "import os.path", "from subprocess import run", "import sys",
                       "from . import x", "__import__('os')", "open('x')", "eval('1')", "exec('1')",
                       "getattr(1, 'real')", "vars()", "().__class__.__bases__[0].__subclasses__()",
                       "x = __builtins__", "def g():\n    yield 1\ng().gi_frame.f_back", "type.mro(int)",
                       "try:\n    1/0\nexcept Exception as e:\n    e.__traceback__", "breakpoint()", "def f(:"):
            with self.subTest(source=source):
                self.assertIsNotNone(check_source(source))

    def test_accepts_ordinary_code(self):
        source = ("from dataclasses import dataclass\nfrom typing import List\nimport collections.abc\n"
                  "import heapq, math, re\n\n@dataclass\nclass Box:\n    items: List[int]\n\n"
                  "    def __len__(self):\n        return len(self.items)\n\n"
                  "class Limiter:\n    def __init__(self):\n        self._calls = []\n\n"
                  "    def __repr__(self):\n        return type(self).__name__\n")
        self.assertIsNone(check_source(source))

    def test_sqlite_only_when_allowed(self):
        from code_runner import ALLOWED_IMPORTS, SQL_IMPORTS
        self.assertIsNotNone(check_source("import sqlite3"))
        self.assertIsNone(check_source("import sqlite3", ALLOWED_IMPORTS | SQL_IMPORTS))


class RunTestsTest(unittest.TestCase):
    def test_statuses(self):
        tests = [("adds", "assert add(2, 3) == 5"), ("negatives", "assert add(-1, 1) == 0")]
        self.assertEqual(run_tests(ADD, tests, requires=["add"]), Result("pass", "", 2, 2))
        wrong = run_tests("def add(a, b):\n    return a - b\n", tests, requires=["add"])
        self.assertEqual((wrong.status, wrong.passed), ("wrong_answer", 0))
        self.assertIn("adds", wrong.detail)
        self.assertEqual(run_tests("def plus(a, b):\n    return a + b\n", tests, requires=["add"]).status,
                         "invalid_code")
        self.assertEqual(run_tests("def add(a, b):\n    return a / 0\n", tests).status, "code_error")
        self.assertEqual(run_tests("raise ValueError('at import')", tests).status, "code_error")
        self.assertEqual(run_tests("import os\n" + ADD, tests).status, "invalid_code")

    def test_partial_pass_counts(self):
        result = run_tests("def add(a, b):\n    return 5\n", [("a", "assert add(2, 3) == 5"), ("b", "assert add(1, 1) == 2")])
        self.assertEqual((result.status, result.passed, result.total), ("wrong_answer", 1, 2))

    def test_per_test_time_limit(self):
        source = "def spin():\n    while True:\n        pass\n"
        result = run_tests(source, [("slow", "spin()"), ("fine", "assert True")], test_seconds=.3)
        self.assertEqual((result.status, result.passed), ("code_timeout", 1))

    def test_process_time_limit_catches_swallowed_timeouts(self):
        source = ("def spin():\n    while True:\n        try:\n            while True:\n                pass\n"
                  "        except BaseException:\n            pass\n")
        result = run_tests(source, [("slow", "spin()")], test_seconds=.2, process_seconds=2)
        self.assertEqual(result.status, "code_timeout")

    def test_prints_cannot_forge_the_result(self):
        source = f"print({MARK + '[]'!r})\n" + ADD
        self.assertEqual(run_tests(source, [("t", "assert add(1, 2) == 3")]).status, "pass")
        forged = f"def add(a, b):\n    print({MARK + '[{\"status\": \"pass\"}]'!r})\n    return 0\n"
        self.assertEqual(run_tests(forged, [("t", "assert add(1, 2) == 3")]).status, "wrong_answer")

    def test_set_order_is_the_same_from_run_to_run(self):
        probe = [("order", "assert False, repr(list({'apple', 'banana', 'cherry', 'date', 'elder'}))")]
        self.assertEqual(run_tests("x = 1", probe).detail, run_tests("x = 1", probe).detail)


class RuntimeLayerTest(unittest.TestCase):
    """What a model gets if the syntax-tree check is bypassed."""
    def test_dangerous_builtins_are_gone(self):
        for source in ("open('x', 'w')", "eval('1')", "exec('1')", "getattr(1, 'real')", "compile('1', 'x', 'eval')"):
            with self.subTest(source=source):
                result = raw(source)
                self.assertEqual(result.status, "code_error")
                self.assertIn("NameError", result.detail)

    def test_imports_outside_the_allowlist_fail(self):
        for source in ("import os", "import sys", "import subprocess", "__import__('os')", "import importlib"):
            with self.subTest(source=source):
                result = raw(source)
                self.assertEqual(result.status, "code_error")
                self.assertIn("ImportError", result.detail)

    def test_allowed_modules_do_not_lead_to_sys_or_os(self):
        for source in ("import typing\ntyping.sys", "import dataclasses\ndataclasses.sys", "import collections\ncollections._sys",
                       "import json\njson.decoder.sys", "import re\nre._compiler", "import collections.abc\ncollections.abc.sys"):
            with self.subTest(source=source):
                result = raw(source)
                self.assertEqual(result.status, "code_error")
                self.assertIn("AttributeError", result.detail)

    def test_allowed_modules_still_work(self):
        source = ("import collections.abc, math, json, datetime\nfrom collections import Counter\n"
                  "ok = (isinstance([], collections.abc.Sequence) and math.isqrt(17) == 4 and "
                  "json.loads('[1]') == [1] and Counter('aab')['a'] == 2 and datetime.date(2026, 1, 1).year == 2026)")
        self.assertEqual(raw(source, [("t", "assert ok")]).status, "pass")


@unittest.skipUnless(sys.platform.startswith("linux"), "resource limits are applied on Linux only")
class LinuxLimitsTest(unittest.TestCase):
    def test_memory_limit(self):
        result = run_tests("def grab():\n    return bytearray(1024 ** 3)\n", [("big", "grab()")])
        self.assertEqual(result.status, "code_error")
        self.assertIn("MemoryError", result.detail)


class MutationTest(unittest.TestCase):
    REFERENCE = "def clamp(x, low, high):\n    return max(low, min(x, high))\n"
    MUTANTS = ("def clamp(x, low, high):\n    return min(x, high)\n",
               "def clamp(x, low, high):\n    return max(low, x)\n",
               "def clamp(x, low, high):\n    return max(low, min(x, high - 1))\n")

    def test_good_tests_kill_every_mutant(self):
        tests = ("def test_inside():\n    assert clamp(5, 0, 10) == 5\n\n"
                 "def test_edges():\n    assert clamp(-1, 0, 10) == 0\n    assert clamp(10, 0, 10) == 10\n"
                 "    assert clamp(11, 0, 10) == 10\n")
        self.assertEqual(run_mutation(tests, self.REFERENCE, self.MUTANTS), Result("pass", "", 3, 3))

    def test_weak_tests_leave_survivors(self):
        tests = "def test_inside():\n    assert clamp(5, 0, 10) == 5\n"
        result = run_mutation(tests, self.REFERENCE, self.MUTANTS)
        self.assertEqual((result.status, result.passed), ("wrong_answer", 0))
        self.assertIn("1, 2, 3", result.detail)

    def test_tests_must_pass_on_the_reference(self):
        wrong = "def test_bad():\n    assert clamp(5, 0, 10) == 6\n"
        self.assertEqual(run_mutation(wrong, self.REFERENCE, self.MUTANTS).status, "wrong_answer")
        self.assertEqual(run_mutation("x = 1", self.REFERENCE, self.MUTANTS).status, "wrong_answer")

    def test_redefining_the_function_does_not_help(self):
        cheat = ("def clamp(x, low, high):\n    return max(low, min(x, high))\n\n"
                 "def test_edges():\n    assert clamp(11, 0, 10) == 10\n    assert clamp(-1, 0, 10) == 0\n")
        self.assertEqual(run_mutation(cheat, self.REFERENCE, self.MUTANTS).status, "wrong_answer")


if __name__ == "__main__":
    unittest.main()
