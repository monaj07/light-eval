"""Run code for the suite 1.8 code track and score it (standard library only).

Two kinds of task run code:

- **Generation and bug fixing:** the model writes Python (untrusted). Hidden test inputs (trusted code)
  are evaluated against it in a jailed child process, which sends back only the encoded outputs. The
  **parent** compares them with the expected values, which never enter the child. Even code that
  escapes every Python-level check can only influence its own outputs, so it can pass only by
  producing the right ones, and nothing it writes reaches the result except built-in exception names.
- **Test generation (mutation testing):** the model writes test cases as data (JSON arguments and
  expected results), not code. Only trusted code runs: the reference and each mutant, on the model's
  arguments. The model's tests pass if they agree with the reference on every case and disagree with
  every mutant on at least one. Functions used this way must return JSON-representable values.

The boundary is the operating system's. Every child runs in its own process inside a jail: bubblewrap
on Linux (no network, separate process and user namespaces, home folders and temporary files hidden,
read-only except an empty working folder), and a deny-by-default sandbox-exec profile on macOS
(only the interpreter, its standard library and system libraries are readable; no network, processes,
signals to others, app launching or system-information queries). The runner checks the jail once and
refuses to run without it. The parent reads at most a few megabytes of the child's output and treats
anything malformed as the code's failure, so no output can crash or exhaust the benchmark runner.

Inside the jail, Python-level checks stop ordinary mistakes and make escapes harder: a syntax-tree check
(`check_source`) and a child whose builtins and imports are restricted. They are defence in depth, not
the boundary; Python cannot be sandboxed from inside itself.

Statuses: `pass`, `invalid_code` (syntax error, forbidden construct, or a required name missing),
`wrong_answer` (an output differs), `code_error` (an exception), `code_timeout`.
"""
import ast
import builtins
import datetime
import decimal
import fractions
import inspect
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
from dataclasses import dataclass

ALLOWED_IMPORTS = frozenset({"math", "itertools", "collections", "functools", "re", "heapq", "bisect", "datetime",
                             "decimal", "fractions", "json", "string", "typing", "dataclasses", "enum", "operator",
                             "statistics", "copy", "__future__"})
SQL_IMPORTS = frozenset({"sqlite3"})
FORBIDDEN_NAMES = frozenset({"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "help", "exit",
                             "quit", "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
                             "__builtins__", "__loader__", "__spec__"})
# Double-underscore names ordinary code needs; any other __x__ attribute or name is rejected.
# Name-mangled privates (__x without trailing underscores) are allowed. __dict__ is not: a class's
# __dict__ hands out __getattribute__, which is getattr under another name.
ALLOWED_DUNDERS = frozenset({"__init__", "__repr__", "__str__", "__eq__", "__ne__", "__lt__", "__le__", "__gt__",
                             "__ge__", "__hash__", "__len__", "__iter__", "__next__", "__contains__", "__getitem__",
                             "__setitem__", "__delitem__", "__enter__", "__exit__", "__call__", "__bool__",
                             "__post_init__", "__name__", "__add__", "__sub__", "__mul__", "__class__", "__slots__",
                             "__all__", "__main__", "__future__"})
# Attributes that reach frames, code objects or the class hierarchy.
FORBIDDEN_ATTRIBUTES = frozenset({"gi_frame", "gi_code", "cr_frame", "cr_code", "ag_frame", "ag_code", "f_back",
                                  "f_globals", "f_locals", "f_builtins", "f_code", "tb_frame", "tb_next", "mro"})
# Built-in types a class pattern may match positionally (they match the subject itself, read no attribute).
SELF_MATCHING = frozenset({"bool", "bytearray", "bytes", "dict", "float", "frozenset", "int", "list", "set", "str",
                           "tuple"})
# Public helpers of allowed modules that read attributes by name or evaluate strings.
HIDDEN = {"operator": ["attrgetter", "methodcaller"], "string": ["Formatter"],
          "typing": ["get_type_hints", "ForwardRef", "get_overloads"],
          "functools": ["singledispatch", "singledispatchmethod"]}
EXCEPTION_NAMES = frozenset(name for name, value in vars(builtins).items()
                            if isinstance(value, type) and issubclass(value, BaseException))
TEST_SECONDS = 5.0       # per test call, enforced in the child with a repeating timer
PROCESS_SECONDS = 30.0   # per child process, enforced from outside
STARTUP_SECONDS = 3.0    # added to a timed test's own limit, for starting the jailed interpreter
OUTPUT_BYTES = 8 * 2 ** 20  # the child may write at most this much (a file-size limit), and no more is read
OUTPUT_LIMIT = 100_000   # characters of the code's own printing kept in the child; never returned
MAX_CASES = 20           # model-written test cases per mutation-testing answer
MAX_CASE_CHARS = 20_000  # their total size as JSON
MAX_DEPTH = 40           # nesting of any value that crosses between parent and child
FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.S)
MARK = "\x1elight-eval-result\x1e"


class SandboxError(RuntimeError):
    """The jail or the resource limits could not be set up; nothing was run."""


@dataclass(frozen=True)
class Result:
    status: str
    detail: str = ""
    passed: int = 0
    total: int = 0


@dataclass(frozen=True)
class Raises:
    """Expected outcome of a test call that must raise this built-in exception (the class itself)."""
    name: str


def extract_code(text, language="python"):
    """The first fenced block in `language`, else the first fenced block, else the whole answer."""
    blocks = FENCE.findall(text or "")
    for tag, body in blocks:
        tag = tag.lower()
        if (tag.startswith("py") if language == "python" else tag == language):
            return body
    return blocks[0][1] if blocks else (text or "")


def dunder_allowed(name):
    if not name.startswith("__"):
        return True
    return name in ALLOWED_DUNDERS or not name.endswith("__")


def check_source(source, allowed_imports=ALLOWED_IMPORTS):
    """Return None if the source may run, else the reason it may not."""
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return f"syntax error on line {error.lineno}: {error.msg}"
    except (ValueError, RecursionError, MemoryError):
        return "the code could not be parsed"
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
        if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or not dunder_allowed(node.id)):
            return f"use of {node.id} is not allowed"
        if isinstance(node, ast.Attribute) and (node.attr in FORBIDDEN_ATTRIBUTES or not dunder_allowed(node.attr)):
            return f"attribute {node.attr} is not allowed"
        if isinstance(node, ast.ClassDef) and (node.keywords or any(
                isinstance(base, ast.Name) and base.id == "type" for base in node.bases)):
            return "metaclasses are not allowed"
        if isinstance(node, ast.MatchClass):
            if any(name.startswith("_") for name in node.kwd_attrs):
                return "match patterns on underscore attributes are not allowed"
            if node.patterns and not (isinstance(node.cls, ast.Name) and node.cls.id in SELF_MATCHING):
                return "positional class patterns are allowed only for built-in types"
    return None


def encode(value, budget=None, depth=0):
    """A JSON form of a result that keeps its exact type, for comparison in the parent. Only exact
    built-in and standard types are encoded, and no method of the value is called beyond these types'
    own; anything else encodes as {"other": true} and anything too large or deep as {"trunc": true},
    and neither ever matches."""
    budget = budget if budget is not None else [100_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 40:
        return {"trunc": True}
    kind = type(value)
    if value is None:
        return None
    if kind is bool:
        return {"b": value}
    if kind is int:
        return {"i": hex(value)}  # hex has no digit limit, unlike str()
    if kind is float:
        return {"f": repr(value)}
    if kind is str:
        return {"s": value}
    if kind is bytes:
        return {"y": value.hex()}
    if kind is list or kind is tuple:
        return {"l" if kind is list else "t": [encode(item, budget, depth + 1) for item in value]}
    if kind is dict:
        pairs = [[encode(key, budget, depth + 1), encode(item, budget, depth + 1)] for key, item in value.items()]
        return {"d": sorted(pairs, key=lambda pair: json.dumps(pair[0], sort_keys=True))}
    if kind is set or kind is frozenset:
        items = [encode(item, budget, depth + 1) for item in value]
        return {"set" if kind is set else "fs": sorted(items, key=lambda item: json.dumps(item, sort_keys=True))}
    if kind is fractions.Fraction:
        return {"q": [hex(value.numerator), hex(value.denominator)]}
    if kind is decimal.Decimal:
        return {"dec": str(value)}
    if kind in (datetime.date, datetime.datetime, datetime.time):
        return {"dt": [kind.__name__, value.isoformat()]}
    if kind is datetime.timedelta:
        return {"td": [value.days, value.seconds, value.microseconds]}
    return {"other": True}


def complete(encoded):
    """True if an encoding holds no {"other"} or {"trunc"} part."""
    if isinstance(encoded, dict):
        return not ({"other", "trunc"} & encoded.keys()) and all(complete(item) for item in encoded.values())
    if isinstance(encoded, list):
        return all(complete(item) for item in encoded)
    return True


def matches(actual, expected, tolerance=0.0):
    """Compare two encoded values. Floats match within `tolerance` (absolute, as in cases.equal_value),
    except inside sets and dictionary keys, which must match exactly."""
    if not (complete(actual) and complete(expected)):
        return False
    return _matches(actual, expected, tolerance)


def _matches(actual, expected, tolerance):
    if isinstance(expected, dict) and "f" in expected:
        if not (isinstance(actual, dict) and "f" in actual):
            return False
        if actual["f"] == expected["f"]:
            return True
        a, e = float(actual["f"]), float(expected["f"])
        return a == e or abs(a - e) <= tolerance
    if isinstance(expected, dict) and isinstance(actual, dict):
        if actual.keys() != expected.keys():
            return False
        if "d" in expected:
            pairs_a, pairs_e = actual["d"], expected["d"]
            return len(pairs_a) == len(pairs_e) and all(
                ka == ke and _matches(va, ve, tolerance) for (ka, va), (ke, ve) in zip(pairs_a, pairs_e))
        if "set" in expected or "fs" in expected:
            return actual == expected
        return all(_matches(actual[key], expected[key], tolerance) for key in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(actual) == len(expected) and all(_matches(a, e, tolerance) for a, e in zip(actual, expected))
    return actual == expected


def as_json_value(encoded):
    """Tuples become lists: model-written expected values arrive as JSON, which has no tuples."""
    if isinstance(encoded, dict):
        if "t" in encoded:
            return {"l": [as_json_value(item) for item in encoded["t"]]}
        return {key: as_json_value(item) if key in ("l", "d", "set", "fs") else item for key, item in encoded.items()}
    if isinstance(encoded, list):
        return [as_json_value(item) for item in encoded]
    return encoded


# The child process. It reads one JSON request from stdin. Its first marked line reports the resource
# limits, written before any code from the request runs; its last reports each call's outcome.
HARNESS = r'''
import builtins, datetime, decimal, fractions, io, json, os, signal, sys, types
MARK = "\x1elight-eval-result\x1e"
request = json.loads(sys.stdin.read())
out = sys.stdout

def report(payload):
    out.write(MARK + json.dumps(payload) + "\n")
    out.flush()

problems = []
import resource
limits = [("RLIMIT_FSIZE", request["output_bytes"]), ("RLIMIT_CORE", 0)]
if request["linux"]:
    cpu = int(request["process_seconds"]) + 5  # above the wall limit, so a timeout always reads as one
    limits += [("RLIMIT_CPU", cpu), ("RLIMIT_AS", 512 * 2 ** 20), ("RLIMIT_NPROC", 0)]
    if os.geteuid() == 0:
        problems.append("running as root, so the process limit does not apply")
for name, value in limits:
    try:
        resource.setrlimit(getattr(resource, name), (value, value))
    except (ValueError, OSError, AttributeError) as error:
        problems.append(name + ": " + str(error))
report({"limits": problems})
if problems:
    sys.exit(0)

ENCODE

real_import = builtins.__import__
real_type = type
allowed = set(request["imports"])
hidden = request["hidden"]
proxies = {}
payload = {"load": None, "missing": [], "outcomes": []}
current = {"fires": 0, "where": "load"}

class TestTimeout(BaseException):
    pass

class Capped(io.StringIO):
    def write(self, text):
        room = request["output_limit"] - self.tell()
        return super().write(text[:room]) if room > 0 else len(text)

def on_alarm(signum, frame):
    # The timer repeats: code that swallows the first TestTimeout gets a few more, then the process
    # reports the timeout itself and ends.
    current["fires"] += 1
    if current["fires"] > 3:
        signal.setitimer(signal.ITIMER_REAL, 0)
        if current["where"] == "load":
            payload["load"] = ["timeout"]
        else:
            payload["outcomes"].append(["timeout"])
        report(payload)
        os._exit(0)
    raise TestTimeout()

def proxy(module):
    """A module's public attributes only, without modules outside the allowlist or hidden helpers."""
    if module.__name__ not in proxies:
        view = types.SimpleNamespace()
        proxies[module.__name__] = view
        skip = set(hidden.get(module.__name__, ()))
        for key, value in vars(module).items():
            if key.startswith("_") or key in skip:
                continue
            if isinstance(value, types.ModuleType):
                if value.__name__.split(".")[0] not in allowed:
                    continue
                value = proxy(value)
            setattr(view, key, value)
    return proxies[module.__name__]

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in allowed:
        raise ImportError("import of " + name + " is not allowed")
    module = real_import(name, globals, locals, fromlist, level)
    if name == "__future__":
        return module
    if not fromlist and "." in name:
        real_import(name, globals, locals, ("_",), level)  # load the submodule so the proxy sees it
    return proxy(module)

def one_argument_type(obj):
    """type(x) only: creating classes with type(name, bases, namespace) is how metaclass tricks start."""
    return real_type(obj)

trusted = dict(vars(builtins))

def kind_of(error):
    """The exception's name if it is exactly that built-in exception class, else a marker."""
    try:
        kind = real_type(error)
        return kind.__name__ if trusted.get(kind.__name__) is kind else "<other>"
    except BaseException:
        return "<other>"

safe = {key: value for key, value in trusted.items() if key not in set(request["forbidden"])}
safe["__import__"] = guarded_import
safe["type"] = one_argument_type
signal.signal(signal.SIGALRM, on_alarm)
sys.stdout = sys.stderr = Capped()

def run(code, namespace, label, where):
    current["fires"], current["where"] = 0, where
    signal.setitimer(signal.ITIMER_REAL, request["test_seconds"], 0.05)
    try:
        exec(compile(code, label, "exec"), namespace)
        return None
    except TestTimeout:
        return ["timeout"]
    except BaseException as error:
        return ["raised", kind_of(error)]
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

try:
    solution = {"__builtins__": safe if request["untrusted"] else trusted, "__name__": "solution"}
    payload["load"] = run(request["source"], solution, "solution", "load")
    if payload["load"] is None:
        payload["missing"] = [name for name in request["requires"] if name not in solution]
    if payload["load"] is None and not payload["missing"]:
        provided = {name: solution[name] for name in request["requires"]}
        for index, (label, code) in enumerate(request["calls"]):
            namespace = {"__builtins__": trusted, "__name__": "tests", "DATA": request["data"], **provided}
            failure = run(code, namespace, label, "call")
            if failure:
                payload["outcomes"].append(failure)
            else:
                try:
                    payload["outcomes"].append(["value", encode(namespace.get("result"))])
                except BaseException:
                    payload["outcomes"].append(["value", {"trunc": True}])
            if failure and request["stop_at_first_failure"]:
                break
except BaseException as error:
    payload["load"] = ["raised", "<harness>"]
report(payload)
'''.replace("ENCODE", inspect.getsource(encode))


def stdlib_paths():
    paths = sysconfig.get_paths()
    return sorted({os.path.realpath(paths["stdlib"]), os.path.realpath(paths["platstdlib"])})


def framework_paths():
    """A macOS framework build (python.org, Homebrew) loads its library from the framework folder."""
    prefix, library = sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX"), sysconfig.get_config_var("LDLIBRARY")
    if not prefix or not library:
        return []
    path = os.path.realpath(os.path.join(prefix, library))
    return [path, os.path.join(os.path.dirname(path), "Resources")] if os.path.exists(path) else []


def jail(folder):
    """The command prefix that runs a process inside the OS sandbox, the interpreter to run, and the
    working folder as the child sees it; `folder` is the only writable place. Raises SandboxError if
    this platform has no supported sandbox."""
    executable = os.path.realpath(sys.executable)
    base = os.path.realpath(sys.base_prefix)
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        stdlib = " ".join(f'(subpath "{path}")' for path in stdlib_paths())
        site = " ".join(f'(subpath "{path}/site-packages")' for path in stdlib_paths())
        framework = " ".join(f'(subpath "{path}")' for path in framework_paths())
        profile = ("(version 1)(deny default)"
                   f'(allow process-exec (literal "{executable}"))'
                   f'(allow file-read* (literal "/") (literal "{executable}") {stdlib} {framework}'
                   f' (regex #"^{re.escape(base)}/lib/[^/]+\\.dylib$")'
                   ' (subpath "/usr/lib") (subpath "/System/Library") (subpath "/private/var/db/dyld")'
                   ' (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random")'
                   f' (subpath "{folder}"))'
                   f"(deny file-read* {site})"
                   "(allow file-read-metadata)"
                   f'(allow file-write* (subpath "{folder}") (literal "/dev/null"))'
                   "(allow signal (target self))")
        return ["sandbox-exec", "-p", profile], executable, folder
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        # /proc is an empty tmpfs, not a new procfs: mounting procfs is refused in containers and some CI
        # runners, and the host's /proc (other processes' environments) must not show through.
        command = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--tmpfs", "/proc"]
        for hidden in ("/home", "/root", "/tmp", "/var/tmp", "/var/lib", "/run", "/mnt", "/media", "/srv",
                       "/opt", "/boot"):
            if os.path.isdir(hidden):
                command += ["--tmpfs", hidden]
        for path in sorted({base, os.path.dirname(os.path.dirname(executable))}):
            command += ["--ro-bind", path, path]  # the interpreter may live under a hidden folder
        command += ["--bind", folder, "/tmp/work", "--chdir", "/tmp/work", "--unshare-all", "--die-with-parent",
                    "--new-session", "--cap-drop", "ALL"]
        return command, executable, "/tmp/work"  # inside the jail's own empty /tmp; the root is read-only
    raise SandboxError("no OS sandbox for model-written code: on Linux install bubblewrap "
                       "(sudo apt install bubblewrap); on macOS sandbox-exec is built in")


def read_capped(handle, limit):
    handle.seek(0)
    return handle.read(limit).decode("utf-8", errors="replace")


def child(request, process_seconds):
    """Run the harness in the jail. Returns (first marked line, last marked line, return code), with
    None for a missing line; returncode is None if the process was stopped for time. The child's output
    goes to files (the child cannot write more than OUTPUT_BYTES), and at most that much is read."""
    with tempfile.TemporaryDirectory(prefix="light-eval-code-") as folder, \
            tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        folder = os.path.realpath(folder)
        prefix, executable, _ = jail(folder)
        # -s -S -B: no user or site packages, no bytecode files. Not -I, which would also ignore
        # PYTHONHASHSEED: a fixed seed keeps set and dict-of-str order the same from run to run.
        command = [*prefix, executable, "-s", "-S", "-B", "-c", HARNESS]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, cwd=folder,
                                   env={"PYTHONHASHSEED": "0"})
        try:
            process.communicate(json.dumps(request).encode(), timeout=process_seconds)
            code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            code = None
        text = read_capped(stdout, OUTPUT_BYTES + 4096)
        error = read_capped(stderr, 4096)
    lines = [line[len(MARK):] for line in text.split("\n") if line.startswith(MARK)]
    first = lines[0] if lines else None
    last = lines[-1] if len(lines) > 1 else None
    return first, last, code, error


def request_for(source, calls, requires, untrusted, data, imports, test_seconds, process_seconds, stop):
    return {"source": source, "calls": [list(call) for call in calls], "requires": list(requires),
            "untrusted": untrusted, "data": data, "imports": sorted(imports),
            "forbidden": sorted(FORBIDDEN_NAMES - {"__builtins__"}), "hidden": HIDDEN,
            "test_seconds": test_seconds, "process_seconds": process_seconds, "output_limit": OUTPUT_LIMIT,
            "output_bytes": OUTPUT_BYTES, "linux": sys.platform.startswith("linux"),
            "stop_at_first_failure": stop}


_JAIL_CHECKED = []


def check_jail():
    """Run a trivial trusted job once, so a missing or broken jail stops the run before any case.
    After this, nothing the child does can raise SandboxError except a limits report written before
    any requested code runs."""
    if not _JAIL_CHECKED:
        request = request_for("x = 1", [["probe", "result = x + 1"]], ["x"], False, None, ALLOWED_IMPORTS,
                              TEST_SECONDS, PROCESS_SECONDS, False)
        first, last, code, error = child(request, PROCESS_SECONDS)
        payload = parse_payload(last, 1)
        if first is None or json.loads(first).get("limits") != [] or payload is None \
                or payload["outcomes"] != [["value", {"i": "0x2"}]]:
            raise SandboxError(f"the sandbox self-check failed: {(first or error or '').strip()[-300:]}")
        _JAIL_CHECKED.append(True)


def valid_encoding(value, depth=0):
    """True if `value` has the shape `encode` produces; anything else came from code, not the harness."""
    if depth > MAX_DEPTH + 2:
        return False
    if value is None:
        return True
    if not isinstance(value, dict) or len(value) != 1:
        return False
    (tag, item), = value.items()
    if tag in ("other", "trunc", "b"):
        return isinstance(item, bool)
    if tag in ("i", "f", "s", "y", "dec"):
        return isinstance(item, str)
    if tag in ("l", "t", "set", "fs"):
        return isinstance(item, list) and all(valid_encoding(part, depth + 1) for part in item)
    if tag == "d":
        return isinstance(item, list) and all(isinstance(pair, list) and len(pair) == 2 and
                                              all(valid_encoding(part, depth + 1) for part in pair) for pair in item)
    if tag == "q" or tag == "dt":
        return isinstance(item, list) and len(item) == 2 and all(isinstance(part, str) for part in item)
    if tag == "td":
        return isinstance(item, list) and len(item) == 3 and all(type(part) is int for part in item)
    return False


def valid_outcome(outcome):
    if not isinstance(outcome, list) or not outcome or outcome[0] not in ("value", "timeout", "raised"):
        return False
    if outcome[0] == "value":
        return len(outcome) == 2 and valid_encoding(outcome[1])
    if outcome[0] == "raised":
        return len(outcome) == 2 and isinstance(outcome[1], str)
    return len(outcome) == 1


def parse_payload(line, calls):
    """The outcome payload, or None if it is missing or not exactly what the harness writes."""
    if line is None:
        return None
    try:
        payload = json.loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(payload, dict) or set(payload) != {"load", "missing", "outcomes"}:
        return None
    load, missing, outcomes = payload["load"], payload["missing"], payload["outcomes"]
    if load is not None and not (isinstance(load, list) and load and load[0] in ("timeout", "raised")
                                 and valid_outcome(load)):
        return None
    if not isinstance(missing, list) or not isinstance(outcomes, list) or len(outcomes) > calls:
        return None
    try:
        if not all(valid_outcome(outcome) for outcome in outcomes):
            return None
    except RecursionError:
        return None
    return payload


def safe_name(name):
    """An exception type name for the result, only if it is a built-in one (results are published)."""
    return name if name in EXCEPTION_NAMES else "an exception"


def evaluate(source, calls, requires, *, untrusted, data=None, imports=ALLOWED_IMPORTS,
             test_seconds=TEST_SECONDS, process_seconds=PROCESS_SECONDS, stop_at_first_failure=False):
    """Run `calls` ((label, code) pairs of trusted code that sets `result`) against `source` in the jail.
    Returns (problem, outcomes): problem is None or a Result for a failure before any call ran."""
    check_jail()
    request = request_for(source, calls, requires, untrusted, data, imports, test_seconds, process_seconds,
                          stop_at_first_failure)
    first, last, code, _ = child(request, process_seconds)
    total = len(calls)
    try:
        limits = json.loads(first).get("limits") if first is not None else None
    except (ValueError, AttributeError):
        limits = None
    if limits:  # written before any requested code ran, so this is the environment, not the code
        raise SandboxError("resource limits could not be set: " + "; ".join(map(str, limits)))
    if limits is None:
        if code is None:
            return Result("code_timeout", "the test process exceeded its time limit", 0, total), []
        return Result("code_error", "the test process ended before running the code", 0, total), []
    payload = parse_payload(last, total)
    if payload is None:
        if code is None:
            return Result("code_timeout", "the test process exceeded its time limit", 0, total), []
        if code < 0 and -code in (signal.SIGKILL, getattr(signal, "SIGXCPU", -1)):
            return Result("code_timeout", "the test process was stopped for using too much time", 0, total), []
        return Result("code_error", "the test process ended without a readable result", 0, total), []
    load = payload["load"]
    if load:
        if load[0] == "timeout":
            return Result("code_timeout", "loading the code took longer than the time limit", 0, total), []
        return Result("code_error", f"loading the code raised {safe_name(load[1])}", 0, total), []
    if payload["missing"]:  # report only names we asked for: nothing the child writes is published
        missing = [name for name in requires if name in payload["missing"]] or ["a required name"]
        return Result("invalid_code", "missing " + ", ".join(missing), 0, total), []
    return None, payload["outcomes"]


def judge(label, outcome, expected, tolerance):
    """None if the outcome is right, else (status, detail)."""
    if outcome is None:
        return ("code_error", f"{label}: no result")
    if isinstance(expected, Raises):
        if outcome == ["raised", expected.name]:
            return None
        return ("wrong_answer", f"{label}: did not raise {expected.name}")
    if outcome[0] == "value":
        return None if matches(outcome[1], encode(expected), tolerance) else ("wrong_answer", f"{label}: wrong result")
    if outcome[0] == "timeout":
        return ("code_timeout", f"{label}: took longer than the time limit")
    return ("code_error", f"{label}: raised {safe_name(outcome[1])}")


def run_tests(source, tests, requires, imports=ALLOWED_IMPORTS, tolerance=0.0, **limits):
    """Score untrusted `source` on hidden `tests`: (label, code, expected) or (label, code, expected, seconds),
    where code is trusted, sees the `requires` names and sets `result`; expected is a value `encode` can
    represent fully, or Raises(name). The expected values never leave this process. A test with its own
    `seconds` runs alone in its own process, timed from outside, so brute force cannot pass it."""
    for test in tests:
        if not isinstance(test[2], Raises) and not complete(encode(test[2])):
            raise ValueError(f"test {test[0]}: the expected value has a type the runner cannot compare")
    reason = check_source(source, imports)
    if reason:
        return Result("invalid_code", reason, 0, len(tests))
    untimed = [test for test in tests if len(test) == 3]
    timed = [test for test in tests if len(test) == 4]
    outcomes = {}
    if untimed:
        problem, results = evaluate(source, [(t[0], t[1]) for t in untimed], requires, untrusted=True,
                                    imports=imports, **limits)
        if problem:
            return Result(problem.status, problem.detail, 0, len(tests))
        outcomes.update({t[0]: outcome for t, outcome in zip(untimed, results)})
    for label, code, _, seconds in timed:
        problem, results = evaluate(source, [(label, code)], requires, untrusted=True, imports=imports,
                                    test_seconds=seconds, process_seconds=seconds + STARTUP_SECONDS)
        if problem and problem.status != "code_timeout":
            return Result(problem.status, problem.detail, 0, len(tests))
        outcomes[label] = ["timeout"] if problem else (results[0] if results else None)
    passed, first = 0, None
    for test in tests:
        failure = judge(test[0], outcomes.get(test[0]), test[2], tolerance)
        if failure:
            first = first or failure
        else:
            passed += 1
    status, detail = first or ("pass", "")
    return Result(status, detail, passed, len(tests))


def json_depth(value, depth=0):
    if depth > MAX_DEPTH:
        return depth
    if isinstance(value, list):
        return max([json_depth(item, depth + 1) for item in value] or [depth])
    if isinstance(value, dict):
        return max([json_depth(item, depth + 1) for item in value.values()] or [depth])
    return depth


def parse_test_cases(answer):
    """Model-written test cases: a JSON list of {"args": [...], "expected": value} (or {"tests": [...]}).
    Returns the list, or a reason it is invalid."""
    cases = answer.get("tests") if isinstance(answer, dict) else answer
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        return f"expected a list of 1 to {MAX_CASES} test cases"
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"args", "expected"} or not isinstance(case["args"], list):
            return 'each test case must be an object with exactly "args" (a list) and "expected"'
    try:
        if len(json.dumps(cases)) > MAX_CASE_CHARS or json_depth(cases) > MAX_DEPTH:
            return "the test cases are too large or too deeply nested"
    except (ValueError, TypeError, RecursionError):
        return "the test cases are not plain JSON"
    return cases


def run_mutation(answer, reference, mutants, function, imports=ALLOWED_IMPORTS, tolerance=0.0, **limits):
    """Mutation testing of model-written test cases (data, see parse_test_cases). They must agree with the
    trusted `reference` on every case, and each trusted mutant must disagree on at least one. Only trusted
    code runs: the implementations, called with the model's JSON arguments. The function must return
    JSON-representable values (lists, string-keyed dicts, numbers, strings, booleans, None)."""
    cases = parse_test_cases(answer)
    if isinstance(cases, str):
        return Result("invalid_code", cases, 0, len(mutants))
    calls = [(f"case {index + 1}", f"result = {function}(*DATA[{index}])") for index in range(len(cases))]
    data = [case["args"] for case in cases]
    expected = [as_json_value(encode(case["expected"])) for case in cases]

    def agrees(outcomes):
        return [outcome is not None and outcome[0] == "value" and matches(as_json_value(outcome[1]), want, tolerance)
                for outcome, want in zip(outcomes + [None] * (len(cases) - len(outcomes)), expected)]

    problem, outcomes = evaluate(reference, calls, [function], untrusted=False, data=data, imports=imports, **limits)
    if problem:  # the model's arguments are too much even for the correct code
        return Result("wrong_answer", f"the correct code cannot run these test cases: {problem.status}",
                      0, len(mutants))
    wrong = [str(index + 1) for index, ok in enumerate(agrees(outcomes)) if not ok]
    if wrong:
        return Result("wrong_answer", f"test cases that are wrong for the correct code: {', '.join(wrong)}",
                      0, len(mutants))
    survivors = []
    for index, mutant in enumerate(mutants):
        problem, outcomes = evaluate(mutant, calls, [function], untrusted=False, data=data, imports=imports, **limits)
        if not problem and all(agrees(outcomes)):
            survivors.append(str(index + 1))
    killed = len(mutants) - len(survivors)
    if survivors:
        return Result("wrong_answer", f"mutants not caught: {', '.join(survivors)}", killed, len(mutants))
    return Result("pass", "", killed, len(mutants))
