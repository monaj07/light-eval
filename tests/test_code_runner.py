"""The code runner for the suite 1.8 code track. Every safeguard has a test that fails without it:
the OS jail, scoring in the parent, the exact-type encoding, mutation testing on data, and the
Python-level layers (defence in depth)."""
import datetime
import json
import math
import os
from decimal import Decimal
from fractions import Fraction
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from code_runner import (MARK, Raises, Result, SandboxError, check_jail, check_source, encode, evaluate,
                         extract_code, jail, matches, run_mutation, run_tests)

ADD = "def add(a, b):\n    return a + b\n"
ADD_TESTS = [("adds", "result = add(2, 3)", 5), ("negatives", "result = add(-1, 1)", 0)]

try:
    check_jail()
    JAIL = True
except SandboxError:
    JAIL = False
needs_jail = unittest.skipUnless(JAIL, "no OS sandbox here (Linux needs bubblewrap)")


class JailPresentTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("LIGHT_EVAL_REQUIRE_JAIL"), "only where the jail must exist (CI)")
    def test_jail_works(self):
        self.assertTrue(JAIL, "the jail self-check failed, so the code-runner tests would be skipped")


class ExtractTest(unittest.TestCase):
    def test_prefers_a_python_block(self):
        text = "Here:\n```text\nnot code\n```\n```python\nx = 1\n```\n```python\nx = 2\n```"
        self.assertEqual(extract_code(text), "x = 1\n")
        for tag in ("py", "python3", "Python", "py3"):
            with self.subTest(tag=tag):
                self.assertEqual(extract_code(f"```text\nno\n```\n```{tag}\ny = 2\n```"), "y = 2\n")

    def test_falls_back_to_any_block_then_the_whole_answer(self):
        self.assertEqual(extract_code("```\nz = 3\n```"), "z = 3\n")
        self.assertEqual(extract_code("z = 3"), "z = 3")
        self.assertEqual(extract_code("```sql\nSELECT 1;\n```", "sql"), "SELECT 1;\n")


class CheckSourceTest(unittest.TestCase):
    def test_rejects_escapes(self):
        for source in (
                "import os", "import os.path", "from subprocess import run", "import sys", "from . import x",
                "__import__('os')", "open('x')", "eval('1')", "exec('1')", "getattr(1, 'real')", "vars()",
                "().__class__.__base__.__subclasses__()", "x = __builtins__", "type.mro(int)",
                "def g():\n    yield 1\ng().gi_frame.f_back",
                "try:\n    1/0\nexcept Exception as e:\n    e.__traceback__", "breakpoint()", "def f(:",
                "import collections\nmatch collections.namedtuple:\n    case object(__globals__=g):\n        pass",
                # Found in review: __dict__ hands out __getattribute__, a getattr by another name.
                "G = '_' * 2\nga = object.__dict__[G + 'getattribute' + G]",
                # Found in the third review: metaclass __instancecheck__ plus positional class patterns.
                "class M(type):\n    pass", "class C(metaclass=M):\n    pass",
                "match x:\n    case C(value):\n        pass"):
            with self.subTest(source=source):
                self.assertIsNotNone(check_source(source))

    def test_accepts_ordinary_code(self):
        source = ("from __future__ import annotations\nfrom dataclasses import dataclass\nfrom typing import List\n"
                  "import collections.abc\nimport heapq, math, re\n__all__ = ['Box']\nSEP = '________'\n\n"
                  "@dataclass\nclass Box:\n    items: List[int]\n\n"
                  "    def __len__(self):\n        return len(self.items)\n\n"
                  "class Counter2:\n    __slots__ = ('__n',)\n\n    def __init__(self):\n        self.__n = 0\n\n"
                  "    def __repr__(self):\n        return self.__class__.__name__ + '__v__'\n\n"
                  "if __name__ == '__main__':\n    print(Box([1]))\n"
                  "match Box([1]):\n    case Box(items=[x]):\n        pass\n")
        self.assertIsNone(check_source(source))

    def test_sqlite_only_when_allowed(self):
        from code_runner import ALLOWED_IMPORTS, SQL_IMPORTS
        self.assertIsNotNone(check_source("import sqlite3"))
        self.assertIsNone(check_source("import sqlite3", ALLOWED_IMPORTS | SQL_IMPORTS))


class EncodeTest(unittest.TestCase):
    """Results are compared in the parent, by exact built-in type, without calling the result's methods."""
    def test_exact_types(self):
        self.assertTrue(matches(encode([1, "a", (2.5, None)]), encode([1, "a", (2.5, None)])))
        for actual, expected in ((5.0, 5), (True, 1), ((1, 2), [1, 2]), ("5", 5), ({1: "a"}, {"1": "a"})):
            with self.subTest(actual=actual):
                self.assertFalse(matches(encode(actual), encode(expected)))

    def test_model_objects_never_match(self):
        class Equal:
            def __eq__(self, other):
                return True

            def __hash__(self):
                return hash(5)
        for value, expected in ((Equal(), 5), ({Equal()}, {5}), ({Equal(): 1}, {5: 1}), ([Equal()], [5])):
            with self.subTest(expected=expected):
                self.assertFalse(matches(encode(value), encode(expected)))

    def test_order_independent_containers_and_float_tolerance(self):
        self.assertTrue(matches(encode({3, 1, 2}), encode({1, 2, 3})))
        self.assertTrue(matches(encode({"b": 1, "a": 2}), encode({"a": 2, "b": 1})))
        self.assertTrue(matches(encode(0.1 + 0.2), encode(0.3), tolerance=1e-9))
        self.assertFalse(matches(encode(0.1 + 0.2), encode(0.3)))
        self.assertFalse(matches(encode(1004.9), encode(1000.0), tolerance=.005))  # absolute, as in cases.py
        self.assertTrue(matches(encode(10.004), encode(10.0), tolerance=.005))
        self.assertFalse(matches(encode({9.9999999, 9.5}), encode({10.0, 9.5}), tolerance=.001))  # sets: exact
        self.assertFalse(matches(encode({9.9999999}), encode({10.0}), tolerance=.001))
        self.assertTrue(matches(encode({"a": 9.9999999}), encode({"a": 10.0}), tolerance=.001))  # dict values: tolerant

    def test_standard_types_and_unrepresentable_values(self):
        for value in (Fraction(1, 3), Decimal("1.10"), datetime.date(2026, 2, 3), datetime.timedelta(hours=3)):
            with self.subTest(value=value):
                self.assertTrue(matches(encode(value), encode(value)))
        self.assertFalse(matches(encode(Decimal("1.1")), encode(Decimal("1.10"))))
        self.assertFalse(matches(encode(object()), encode(object())))  # "other" never matches, even itself
        self.assertFalse(matches(encode(Fraction(1, 3)), encode(1 / 3)))

    def test_large_values_never_match_by_truncation(self):
        big = math.factorial(2000)
        self.assertFalse(matches(encode(big + 1), encode(big)))
        self.assertTrue(matches(encode(big), encode(big)))
        self.assertFalse(matches(encode(list(range(100000)) + [999]), encode(list(range(100000)) + [0])))


@needs_jail
class JailTest(unittest.TestCase):
    """The OS jail on its own: plain Python with every module available tries to get out."""
    PROBE = ("import os, socket, subprocess\n"
             "def attempt(f):\n    try:\n        f()\n        return 'allowed'\n    except BaseException:\n        return 'blocked'\n"
             "print('home', attempt(lambda: os.listdir({home!r})))\n"
             "print('repo', attempt(lambda: open({repo!r}).read()))\n"
             "print('tmp', attempt(lambda: open('/tmp/light-eval-jail-probe', 'w').write('x')))\n"
             "print('net', attempt(lambda: socket.create_connection(('1.1.1.1', 80), 2)))\n"
             "print('spawn', attempt(lambda: subprocess.run(['/bin/sh', '-c', 'true'], check=True)))\n"
             "print('fork', attempt(lambda: os.waitpid(os.fork(), 0) if True else None))\n"
             "print('kill', attempt(lambda: os.kill(os.getppid(), 0)))\n"
             "print('work', attempt(lambda: open('ok.txt', 'w').write('x')))\n")

    def jailed(self, code):
        with tempfile.TemporaryDirectory() as folder:
            folder = os.path.realpath(folder)
            prefix, executable, _ = jail(folder)
            return subprocess.run([*prefix, executable, "-s", "-S", "-B", "-c", code], capture_output=True,
                                  text=True, cwd=folder, env={}, timeout=30)

    def test_jail_blocks_home_repo_tmp_network_processes_and_signals(self):
        repo = str(Path(__file__).resolve().parents[1] / "README.md")
        completed = self.jailed(self.PROBE.format(home=os.path.expanduser("~"), repo=repo))
        seen = dict(line.split() for line in completed.stdout.splitlines() if line.count(" ") == 1)
        expected = {"repo": "blocked", "tmp": "blocked", "net": "blocked", "spawn": "blocked", "work": "allowed"}
        if sys.platform == "darwin":
            expected.update(home="blocked", fork="blocked", kill="blocked")
        else:
            expected.pop("tmp")  # an empty throwaway tmpfs; the real /tmp is hidden
            expected.pop("spawn")  # on Linux the harness's process limit blocks this: see the next test
        self.assertEqual({key: seen.get(key) for key in expected}, expected, completed.stderr)

    def test_the_harness_cannot_start_processes(self):
        # Trusted source stands in for escaped code: it has the real os and subprocess modules.
        source = ("import subprocess\ntry:\n    subprocess.run(['/bin/sh', '-c', 'true'], check=True)\n"
                  "    spawned = 'allowed'\nexcept Exception:\n    spawned = 'blocked'\n")
        problem, outcomes = evaluate(source, [("t", "result = spawned")], ["spawned"], untrusted=False)
        self.assertIsNone(problem)
        self.assertEqual(outcomes, [["value", {"s": "blocked"}]])

    @unittest.skipUnless(sys.platform == "darwin", "macOS: programs that would act outside the sandbox")
    def test_exec_of_other_programs_is_blocked(self):
        for program in (["/usr/bin/open", "-g", "-a", "Chess"], ["/usr/bin/pbpaste"], ["/bin/sh", "-c", "echo ran"]):
            with self.subTest(program=program[0]):
                completed = self.jailed(f"import os\nos.execv({program[0]!r}, {program!r})")
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("PermissionError", completed.stderr)


class NoJailTest(unittest.TestCase):
    def test_refuses_to_run_without_a_jail(self):
        import code_runner
        original, checked = code_runner.shutil.which, list(code_runner._JAIL_CHECKED)
        code_runner.shutil.which = lambda name: None
        code_runner._JAIL_CHECKED.clear()
        try:
            with self.assertRaises(SandboxError):
                run_tests(ADD, ADD_TESTS, ["add"])
        finally:
            code_runner.shutil.which = original
            code_runner._JAIL_CHECKED[:] = checked


@needs_jail
class RunTestsTest(unittest.TestCase):
    def test_statuses(self):
        self.assertEqual(run_tests(ADD, ADD_TESTS, ["add"]), Result("pass", "", 2, 2))
        wrong = run_tests("def add(a, b):\n    return a - b\n", ADD_TESTS, ["add"])
        self.assertEqual((wrong.status, wrong.passed, wrong.detail), ("wrong_answer", 0, "adds: wrong result"))
        self.assertEqual(run_tests("def plus(a, b):\n    return a + b\n", ADD_TESTS, ["add"]),
                         Result("invalid_code", "missing add", 0, 2))
        self.assertEqual(run_tests("def add(a, b):\n    return a / 0\n", ADD_TESTS, ["add"]).detail,
                         "adds: raised ZeroDivisionError")
        self.assertEqual(run_tests("raise ValueError('at import')", ADD_TESTS, ["add"]).status, "code_error")
        self.assertEqual(run_tests("import os\n" + ADD, ADD_TESTS, ["add"]).status, "invalid_code")

    def test_expected_exceptions(self):
        source = "def parse(text):\n    if not text:\n        raise ValueError('empty')\n    return int(text)\n"
        tests = [("empty", "result = parse('')", Raises("ValueError")), ("ok", "result = parse('7')", 7)]
        self.assertEqual(run_tests(source, tests, ["parse"]).status, "pass")
        self.assertEqual(run_tests("def parse(text):\n    return 0\n", tests, ["parse"]).status, "wrong_answer")

    def test_model_exception_names_are_not_published(self):
        source = "class SecretContents(Exception):\n    pass\n\ndef add(a, b):\n    raise SecretContents()\n"
        self.assertEqual(run_tests(source, ADD_TESTS, ["add"]).detail, "adds: raised an exception")

    def test_time_limits(self):
        source = "def spin():\n    while True:\n        pass\n"
        result = run_tests(source, [("slow", "result = spin()", 1), ("fine", "result = 1", 1)], ["spin"], test_seconds=.3)
        self.assertEqual((result.status, result.passed), ("code_timeout", 1))
        swallow = ("def spin():\n    while True:\n        try:\n            while True:\n                pass\n"
                   "        except BaseException:\n            pass\n")
        result = run_tests(swallow, [("slow", "result = spin()", 1)], ["spin"], test_seconds=.2, process_seconds=2)
        self.assertEqual((result.status, result.total), ("code_timeout", 1))

    def test_printing_is_captured_and_capped(self):
        fake = MARK + json.dumps({"load": None, "missing": [], "outcomes": [["value", {"i": "3"}]]})
        source = f"def add(a, b):\n    print({fake!r})\n    for _ in range(100000):\n        print('x' * 100)\n    return 0\n"
        self.assertEqual(run_tests(source, [("t", "result = add(1, 2)", 3)], ["add"]).status, "wrong_answer")

    def test_swallowing_the_timer_does_not_beat_it(self):
        source = ("def total(n):\n    s = i = 0\n    while i < n:\n        try:\n            while i < n:\n"
                  "                s += i\n                i += 1\n        except BaseException:\n            pass\n    return s\n")
        result = run_tests(source, [("big", "result = total(10 ** 8)", sum(range(10 ** 8)))], ["total"], test_seconds=.5)
        self.assertEqual(result.status, "code_timeout")

    def test_timed_tests_run_alone_with_their_own_limit(self):
        brute = "def count(n):\n    return sum(1 for _ in range(n))\n"
        fast = "def count(n):\n    return n\n"
        tests = [("small", "result = count(5)", 5), ("large", "result = count(10 ** 9)", 10 ** 9, 1.0)]
        self.assertEqual(run_tests(fast, tests, ["count"]), Result("pass", "", 2, 2))
        self.assertEqual(run_tests(brute, tests, ["count"]).detail, "large: took longer than the time limit")

    def test_unrepresentable_expected_values_are_a_case_bug(self):
        with self.assertRaises(ValueError):
            run_tests(ADD, [("t", "result = add(1, 2)", object())], ["add"])

    def test_standard_types_pass_and_fail_correctly(self):
        source = "from fractions import Fraction\n\ndef third():\n    return Fraction(1, 3)\n\ndef fake():\n    return object()\n"
        self.assertEqual(run_tests(source, [("t", "result = third()", Fraction(1, 3))], ["third"]).status, "pass")
        self.assertEqual(run_tests(source, [("t", "result = fake()", Fraction(1, 3))], ["fake"]).status, "wrong_answer")

    def test_set_order_is_the_same_from_run_to_run(self):
        source = "def order():\n    return list({'apple', 'banana', 'cherry', 'date', 'elder'})\n"
        first = evaluate(source, [("t", "result = order()")], ["order"], untrusted=True)
        self.assertEqual(first, evaluate(source, [("t", "result = order()")], ["order"], untrusted=True))


@needs_jail
class ScoringIntegrityTest(unittest.TestCase):
    """Wrong code must not pass, even if it escapes the Python layers (found in review)."""
    def test_always_equal_and_hash_tricks_fail(self):
        cls = "class A:\n    def __eq__(self, other):\n        return True\n\n    def __hash__(self):\n        return hash(5)\n\n"
        for body, expected in (("return A()", 5), ("return {A()}", {5}), ("return {A(): 1}", {5: 1})):
            with self.subTest(body=body):
                source = cls + f"def f():\n    {body}\n"
                self.assertEqual(run_tests(source, [("t", "result = f()", expected)], ["f"]).status, "wrong_answer")

    def test_shadowed_builtins_do_not_reach_the_tests(self):
        # A correct answer that happens to define its own sorted() must not break tests that use the real one.
        source = "def sorted(x):\n    return [9]\n\ndef order(x):\n    return list(x)\n"
        tests = [("t", "result = sorted(order([3, 1, 2]))", [1, 2, 3])]
        self.assertEqual(run_tests(source, tests, ["order"]).status, "pass")

    def test_builtin_subclasses_do_not_pass_as_builtins(self):
        for body, expected in (("class I(int):\n    def __str__(self):\n        return '5'\n\n    __repr__ = __str__\n\n"
                                "def f():\n    return I(7)\n", 5),
                               ("class S(set):\n    def __iter__(self):\n        return iter([5])\n\n"
                                "def f():\n    return S({9})\n", {5}),
                               ("class L(list):\n    def __iter__(self):\n        return iter([5])\n\n"
                                "def f():\n    return L([9])\n", [5]),
                               ("class L(list):\n    def __iter__(self):\n        return iter([5])\n\n"
                                "def f():\n    return L([9])\n", (5,)),
                               ("class S(set):\n    def __iter__(self):\n        return iter([5])\n\n"
                                "def f():\n    return S({9})\n", frozenset({5}))):
            with self.subTest(expected=expected):
                self.assertEqual(run_tests(body, [("t", "result = f()", expected)], ["f"]).status, "wrong_answer")

    def test_an_escaped_child_cannot_forge_a_pass_without_the_answer(self):
        # Simulate a full escape with trusted code that writes its own result line to the real stdout.
        # The child's claims are only outputs: the parent compares them with expected values it never sent.
        forged = MARK + json.dumps({"load": None, "missing": [], "outcomes": [["value", {"i": "999"}]] * 2})
        source = f"import sys\nsys.__stdout__.write({forged!r} + '\\n')\nsys.__stdout__.flush()\nimport os\nos._exit(0)\n"
        problem, outcomes = evaluate(source, [("a", "result = 1"), ("b", "result = 1")], [], untrusted=False)
        self.assertIsNone(problem)
        self.assertEqual(outcomes, [["value", {"i": "999"}]] * 2)  # taken as claimed outputs, not as a verdict
        self.assertFalse(matches(outcomes[0][1], encode(5)))

    def test_forged_missing_names_are_not_published(self):
        forged = MARK + json.dumps({"load": None, "missing": ["/etc/passwd: root:x:0:0"], "outcomes": []})
        source = f"import sys\nsys.__stdout__.write({forged!r} + '\\n')\nsys.__stdout__.flush()\nimport os\nos._exit(0)\n"
        problem, _ = evaluate(source, [("a", "result = 1")], ["add"], untrusted=False)
        self.assertEqual(problem.detail, "missing a required name")

    def test_own_exception_class_with_a_builtin_name_does_not_count(self):
        source = "class ValueError(Exception):\n    pass\n\ndef parse(text):\n    raise ValueError()\n"
        result = run_tests(source, [("t", "result = parse('')", Raises("ValueError"))], ["parse"])
        self.assertEqual(result.status, "wrong_answer")

    def test_garbage_from_an_escaped_child_never_crashes_the_parent(self):
        # Found in the third review: each of these, written by escaped code, crashed the runner.
        bodies = ["bytes([255, 254, 10])", f"({MARK!r} + '{{\"load\": null, \"missing\": [], \"outcomes\": [[\"value\"]]}}\\n').encode()",
                  f"({MARK!r} + '{{\"load\": null, \"missing\": [], \"outcomes\": [[\"raised\", [\"x\"]]]}}\\n').encode()",
                  f"({MARK!r} + '{{\"load\": null, \"missing\": [], \"outcomes\": [[\"value\", {{\"f\": []}}]]}}\\n').encode()",
                  f"({MARK!r} + '[1, 2]\\n').encode()", f"({MARK!r} + '{{\"load\": null}}\\n').encode()", f"({MARK!r} + '{{\\n').encode()",
                  f"({MARK!r} + '[' * 100000 + '\\n').encode()", "b'x' * (64 * 2 ** 20)"]
        for body in bodies:
            with self.subTest(body=body[:50]):
                source = f"import os\nos.write(1, {body})\nos._exit(0)\n"
                problem, _ = evaluate(source, [("a", "result = 1")], [], untrusted=False)
                self.assertEqual(problem.status, "code_error")

    def test_a_child_that_dies_is_a_code_error_not_a_sandbox_error(self):
        problem, _ = evaluate("import os\nos._exit(3)\n", [("a", "result = 1")], [], untrusted=False)
        self.assertEqual(problem.status, "code_error")


@needs_jail
class RuntimeLayerTest(unittest.TestCase):
    """What untrusted code gets if the syntax-tree check is bypassed (defence in depth)."""
    def loads(self, source):
        problem, _ = evaluate(source, [("t", "result = 1")], [], untrusted=True)
        return problem.detail if problem else "loaded"

    def test_dangerous_builtins_are_gone(self):
        for source in ("open('x', 'w')", "eval('1')", "exec('1')", "getattr(1, 'real')", "compile('1', 'x', 'eval')"):
            with self.subTest(source=source):
                self.assertEqual(self.loads(source), "loading the code raised NameError")

    def test_imports_outside_the_allowlist_fail(self):
        for source in ("import os", "import sys", "import subprocess", "__import__('os')", "import importlib"):
            with self.subTest(source=source):
                self.assertIn(self.loads(source), ("loading the code raised ImportError", "loading the code raised NameError"))

    def test_allowed_modules_do_not_lead_to_sys_os_or_attribute_helpers(self):
        for source in ("import typing\ntyping.sys", "import dataclasses\ndataclasses.sys", "import collections\ncollections._sys",
                       "import json\njson.decoder.sys", "import re\nre._compiler", "import collections.abc\ncollections.abc.sys",
                       "import operator\noperator.attrgetter", "import operator\noperator.methodcaller",
                       "import string\nstring.Formatter", "import typing\ntyping.get_type_hints",
                       "import functools\nfunctools.singledispatch", "from operator import attrgetter"):
            with self.subTest(source=source):
                self.assertIn(self.loads(source), ("loading the code raised AttributeError", "loading the code raised ImportError"))

    def test_three_argument_type_is_gone(self):
        self.assertEqual(self.loads("C = type('C', (), {})"), "loading the code raised TypeError")
        self.assertEqual(self.loads("ok = type(1) is int"), "loaded")

    def test_allowed_modules_still_work(self):
        source = ("from __future__ import annotations\nimport collections.abc, math, json, datetime, operator\n"
                  "from collections import Counter\n"
                  "ok = (isinstance([], collections.abc.Sequence) and math.isqrt(17) == 4 and "
                  "json.loads('[1]') == [1] and Counter('aab')['a'] == 2 and datetime.date(2026, 1, 1).year == 2026 "
                  "and operator.itemgetter(1)([5, 6]) == 6)")
        self.assertEqual(run_tests(source, [("t", "result = ok", True)], ["ok"]).status, "pass")

    def test_sqlite_when_allowed(self):
        from code_runner import ALLOWED_IMPORTS, SQL_IMPORTS
        source = "import sqlite3\n\ndef two():\n    return sqlite3.connect(':memory:').execute('select 1 + 1').fetchone()[0]\n"
        self.assertEqual(run_tests(source, [("t", "result = two()", 2)], ["two"], ALLOWED_IMPORTS | SQL_IMPORTS).status, "pass")


@unittest.skipUnless(JAIL and sys.platform.startswith("linux"), "resource limits are applied on Linux only")
class LinuxLimitsTest(unittest.TestCase):
    def test_memory_limit(self):
        result = run_tests("def grab():\n    return len(bytearray(1024 ** 3))\n", [("big", "result = grab()", 1)], ["grab"])
        self.assertEqual(result.detail, "big: raised MemoryError")

    def test_cpu_limit_reads_as_a_timeout(self):
        source = ("def spin():\n    while True:\n        try:\n            while True:\n                pass\n"
                  "        except BaseException:\n            pass\n")
        result = run_tests(source, [("slow", "result = spin()", 1)], ["spin"], test_seconds=.2, process_seconds=3)
        self.assertEqual(result.status, "code_timeout")


@needs_jail
class MutationTest(unittest.TestCase):
    """Model-written tests are data; only the trusted reference and mutants run."""
    REFERENCE = "def clamp(x, low, high):\n    return max(low, min(x, high))\n"
    MUTANTS = ("def clamp(x, low, high):\n    return min(x, high)\n",
               "def clamp(x, low, high):\n    return max(low, x)\n",
               "def clamp(x, low, high):\n    return max(low, min(x, high - 1))\n")

    def mutation(self, answer, mutants=None, **limits):
        return run_mutation(answer, self.REFERENCE, self.MUTANTS if mutants is None else mutants, "clamp", **limits)

    def test_good_tests_kill_every_mutant(self):
        answer = {"tests": [{"args": [5, 0, 10], "expected": 5}, {"args": [-1, 0, 10], "expected": 0},
                            {"args": [10, 0, 10], "expected": 10}, {"args": [11, 0, 10], "expected": 10}]}
        self.assertEqual(self.mutation(answer), Result("pass", "", 3, 3))

    def test_weak_tests_leave_survivors(self):
        result = self.mutation([{"args": [5, 0, 10], "expected": 5}])
        self.assertEqual((result.status, result.passed, result.detail), ("wrong_answer", 0, "mutants not caught: 1, 2, 3"))

    def test_wrong_expectations_fail(self):
        result = self.mutation([{"args": [5, 0, 10], "expected": 6}, {"args": [11, 0, 10], "expected": 10}])
        self.assertEqual((result.status, result.detail), ("wrong_answer", "test cases that are wrong for the correct code: 1"))

    def test_malformed_answers_are_invalid(self):
        for answer in ([], {"tests": "x"}, [{"args": 5, "expected": 1}], [{"args": [1], "expected": 1, "x": 0}],
                       [{"args": [1, 2, 3], "expected": 1}] * 21):
            with self.subTest(answer=str(answer)[:40]):
                self.assertEqual(self.mutation(answer).status, "invalid_code")

    def test_looping_and_crashing_mutants_count_as_caught(self):
        looping = "def clamp(x, low, high):\n    while x > high:\n        pass\n    return max(low, x)\n"
        crashing = "def clamp(x, low, high):\n    return [][x]\n"
        answer = [{"args": [11, 0, 10], "expected": 10}, {"args": [5, 0, 10], "expected": 5}]
        self.assertEqual(self.mutation(answer, [looping, crashing], test_seconds=.3, process_seconds=5),
                         Result("pass", "", 2, 2))

    def test_arguments_too_much_for_the_reference_are_the_models_problem(self):
        reference = "def total(n):\n    s = 0\n    for i in range(n):\n        s += i\n    return s\n"
        result = run_mutation([{"args": [10 ** 12], "expected": 0}] * 3, reference, [reference], "total",
                              test_seconds=.3, process_seconds=3)
        self.assertEqual(result.status, "wrong_answer")
        nested = []
        for _ in range(3000):
            nested = [nested]
        deep = [{"args": [1], "expected": nested}]
        self.assertEqual(run_mutation(deep, reference, [reference], "total").status, "invalid_code")

    def test_tuples_compare_as_json_lists(self):
        reference = "def pair(a):\n    return (a, a + 1)\n"
        mutant = "def pair(a):\n    return (a, a)\n"
        result = run_mutation([{"args": [1], "expected": [1, 2]}], reference, [mutant], "pair")
        self.assertEqual(result, Result("pass", "", 1, 1))


if __name__ == "__main__":
    unittest.main()
