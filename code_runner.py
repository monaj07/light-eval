"""Run model-written Python against hidden tests, for the suite 1.8 code track (standard library only).

Model code is untrusted. It goes through two layers before and while it runs:

1. A syntax-tree check (`check_source`) rejects imports outside an allowlist, calls such as `open`,
   `eval` and `getattr`, and double-underscore attributes and names that lead back to the interpreter
   (`().__class__.__subclasses__()` and the like).
2. It runs in a separate Python process in an empty temporary folder, with an empty environment, no
   site packages, builtins stripped of the same dangerous names, an `__import__` that only admits the
   allowlist, a per-test time limit and a wall-clock limit for the whole process; on Linux also limits
   on CPU time, memory, file size and process count.

Neither layer is a complete sandbox on its own, which is why code cases run only on the dedicated
benchmark machine (docs/SUITE_1_8_DESIGN.md). The tests themselves are trusted code from the suite.

Statuses: `pass`, `invalid_code` (syntax error, forbidden construct, or the required name missing),
`wrong_answer` (a test failed), `code_error` (an exception), `code_timeout`.
"""
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass

ALLOWED_IMPORTS = frozenset({"math", "itertools", "collections", "functools", "re", "heapq", "bisect", "datetime",
                             "decimal", "fractions", "json", "string", "typing", "dataclasses", "enum", "operator",
                             "statistics", "copy"})
SQL_IMPORTS = frozenset({"sqlite3"})
FORBIDDEN_NAMES = frozenset({"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "help", "exit",
                             "quit", "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
                             "__builtins__", "__loader__", "__spec__"})
# Dunder attributes a normal class needs; every other __x__ attribute is rejected.
ALLOWED_DUNDERS = frozenset({"__init__", "__repr__", "__str__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__",
                             "__ge__", "__hash__", "__len__", "__iter__", "__next__", "__contains__", "__getitem__",
                             "__setitem__", "__delitem__", "__enter__", "__exit__", "__call__", "__bool__",
                             "__post_init__", "__name__", "__add__", "__sub__", "__mul__"})
# Attributes that reach frames, code objects or the class hierarchy, where the harness's own globals live.
FORBIDDEN_ATTRIBUTES = frozenset({"gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code", "f_back",
                                  "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame", "tb_next", "mro"})
TEST_SECONDS = 5.0       # per test, enforced in the child with an interval timer
PROCESS_SECONDS = 30.0   # the whole child process, enforced from outside
FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.S)


@dataclass(frozen=True)
class Result:
    status: str
    detail: str = ""
    passed: int = 0
    total: int = 0


def extract_code(text, language="python"):
    """The first fenced block in `language`, else the first fenced block, else the whole answer."""
    blocks = FENCE.findall(text or "")
    for tag, body in blocks:
        if tag.lower() in ({language, "py"} if language == "python" else {language}):
            return body
    return blocks[0][1] if blocks else (text or "")


def check_source(source, allowed_imports=ALLOWED_IMPORTS):
    """Return None if the source may run, else the reason it may not."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return f"syntax error on line {error.lineno}: {error.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return "relative import"
            names = [node.module or ""]
        else:
            names = []
        for name in names:
            if name.split(".")[0] not in allowed_imports:
                return f"import of {name} is not allowed"
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            return f"use of {node.id} is not allowed"
        if isinstance(node, ast.Attribute) and (node.attr in FORBIDDEN_ATTRIBUTES or (
                node.attr.startswith("__") and node.attr not in ALLOWED_DUNDERS)):
            return f"attribute {node.attr} is not allowed"
        if isinstance(node, ast.Name) and node.id.startswith("__") and node.id.endswith("__") \
                and node.id not in ALLOWED_DUNDERS:
            return f"name {node.id} is not allowed"
    return None


# The child process. It reads one JSON request from stdin and prints one JSON line, marked so that
# anything the model's code prints cannot be mistaken for it (its prints are captured anyway).
HARNESS = r'''
import builtins, io, json, signal, sys, types
MARK = "\x1elight-eval-result\x1e"
request = json.loads(sys.stdin.read())
if request["linux_limits"]:
    import resource
    for name, value in (("RLIMIT_CPU", 20), ("RLIMIT_AS", 512 * 2 ** 20), ("RLIMIT_FSIZE", 2 ** 20),
                        ("RLIMIT_NPROC", 0), ("RLIMIT_CORE", 0)):
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (ValueError, OSError, AttributeError):
            pass
real_import = builtins.__import__
allowed = set(request["imports"])
proxies = {}

def proxy(module):
    """A module's public attributes only; modules outside the allowlist (sys, os) are dropped."""
    if module.__name__ not in proxies:
        view = types.SimpleNamespace()
        proxies[module.__name__] = view
        for key, value in vars(module).items():
            if key.startswith("_"):
                continue
            if isinstance(value, types.ModuleType):
                if value.__name__.split(".")[0] not in allowed:
                    continue
                value = proxy(value)
            setattr(view, key, value)
    return proxies[module.__name__]

class TestTimeout(BaseException):
    pass

def on_alarm(signum, frame):
    raise TestTimeout()

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in allowed:
        raise ImportError("import of " + name + " is not allowed")
    module = real_import(name, globals, locals, fromlist, level)
    if not fromlist and "." in name:
        real_import(name, globals, locals, ("_",), level)  # load the submodule so the proxy sees it
    return proxy(module)

safe = {key: value for key, value in vars(builtins).items() if key not in set(request["forbidden"])}
safe["__import__"] = guarded_import
signal.signal(signal.SIGALRM, on_alarm)
out = sys.stdout
sys.stdout = sys.stderr = io.StringIO()

def run(code, namespace, label):
    signal.setitimer(signal.ITIMER_REAL, request["test_seconds"])
    try:
        exec(compile(code, label, "exec"), namespace)
        return None
    except TestTimeout:
        return ("code_timeout", label + ": took longer than the time limit")
    except AssertionError as error:
        return ("wrong_answer", label + ": " + (str(error) or "assertion failed")[:300])
    except BaseException as error:
        return ("code_error", label + ": " + type(error).__name__ + ": " + str(error)[:300])
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

def namespace():
    return {"__builtins__": safe, "__name__": "solution"}

results = []
for job in request["jobs"]:
    ns = namespace()
    failure = run(job["source"], ns, "solution")
    if failure:
        results.append({"status": failure[0], "detail": failure[1], "passed": 0, "total": len(job["tests"])})
        continue
    missing = [name for name in job["requires"] if name not in ns]
    if missing:
        results.append({"status": "invalid_code", "detail": "missing " + ", ".join(missing), "passed": 0,
                        "total": len(job["tests"])})
        continue
    if job["discover"]:
        ns_tests = [name for name in list(ns) if name.startswith("test_") and callable(ns[name])]
        tests = [[name, name + "()"] for name in ns_tests]
    else:
        tests = job["tests"]
    passed, first = 0, None
    for label, code in tests:
        failure = run(code, ns, label)
        if failure:
            first = first or failure
        else:
            passed += 1
    status, detail = first or ("pass", "")
    results.append({"status": status, "detail": detail, "passed": passed, "total": len(tests)})
sys.stdout = out
print(MARK + json.dumps(results))
'''
MARK = "\x1elight-eval-result\x1e"


def run_jobs(jobs, imports=ALLOWED_IMPORTS, process_seconds=PROCESS_SECONDS, test_seconds=TEST_SECONDS):
    """Run jobs in one child process. Each job is {source, tests, requires, discover}."""
    request = {"jobs": jobs, "imports": sorted(imports), "forbidden": sorted(FORBIDDEN_NAMES - {"__builtins__"}),
               "test_seconds": test_seconds,
               "linux_limits": sys.platform.startswith("linux")}
    with tempfile.TemporaryDirectory(prefix="light-eval-code-") as folder:
        # -s -S: no user or site packages. Not -I, which would also ignore PYTHONHASHSEED: a fixed hash
        # seed keeps set and dict-of-str iteration order the same from run to run. The environment is
        # otherwise empty, and the working folder (sys.path[0] for -c) is an empty temporary folder.
        # Resource limits are set by the child itself: preexec_fn is unsafe in a process with threads.
        command = [sys.executable, "-s", "-S", "-B", "-c", HARNESS]
        try:
            completed = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True,
                                       cwd=folder, env={"PYTHONHASHSEED": "0"}, timeout=process_seconds)
        except subprocess.TimeoutExpired:
            return [Result("code_timeout", "the test process exceeded its time limit")] * len(jobs)
    for line in reversed(completed.stdout.split("\n")):  # not splitlines(), which splits at \x1e
        if line.startswith(MARK):
            return [Result(**item) for item in json.loads(line[len(MARK):])]
    reason = (completed.stderr.strip().splitlines() or [f"exit status {completed.returncode}"])[-1]
    return [Result("code_error", f"the test process failed: {reason[:300]}")] * len(jobs)


def run_tests(source, tests, requires=(), imports=ALLOWED_IMPORTS, **limits):
    """Run hidden `tests` ((label, code) pairs, trusted) against untrusted `source`."""
    reason = check_source(source, imports)
    if reason:
        return Result("invalid_code", reason, 0, len(tests))
    job = {"source": source, "tests": [list(test) for test in tests], "requires": list(requires), "discover": False}
    return run_jobs([job], imports, **limits)[0]


def run_mutation(test_source, reference, mutants, imports=ALLOWED_IMPORTS, **limits):
    """Mutation testing of untrusted tests: every test_* function in `test_source` must pass on the
    trusted `reference`, and each trusted mutant must fail at least one of them."""
    reason = check_source(test_source, imports)
    if reason:
        return Result("invalid_code", reason, 0, len(mutants))
    jobs = [{"source": implementation + "\n\n" + test_source, "tests": [], "requires": [], "discover": True}
            for implementation in (reference, *mutants)]
    first, *rest = run_jobs(jobs, imports, **limits)
    if first.status != "pass" or first.total == 0:
        detail = "no test_ functions found" if first.total == 0 and first.status == "pass" else first.detail
        return Result("wrong_answer" if first.status == "pass" else first.status,
                      f"tests must pass on the correct code: {detail}", 0, len(mutants))
    killed = sum(result.status != "pass" for result in rest)
    survivors = [str(index + 1) for index, result in enumerate(rest) if result.status == "pass"]
    if survivors:
        return Result("wrong_answer", f"mutants not caught: {', '.join(survivors)}", killed, len(mutants))
    return Result("pass", "", killed, len(mutants))
