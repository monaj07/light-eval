"""Suite 1.8 code track (docs/SUITE_1_8_DESIGN.md), staged outside the live suite like suite_1_8.py.

Twenty hard cases in four kinds:

- generate (6) and fix (5): the model writes Python; hidden tests run it in the jail (code_runner.run_tests).
  Every case has a reference solution and plausible wrong ones, and tests/test_suite_1_8_code.py checks
  that the reference passes and every wrong one fails. A fix case's first wrong solution is the buggy
  code shown in the prompt. Expected values are computed from the reference, never typed by hand.
- predict (4): the model says what a short program prints; the expected answer is checked by running
  the program. inverse (1): the model finds an input; the trusted function is run on it.
- testgen (4): the model writes test cases as JSON data; they must agree with the reference and catch
  every mutant (code_runner.run_mutation). A reference set of test cases checks that this is possible.

Timed tests (a fourth element, in seconds) run alone and are sized so the reference takes well under a
second on the Mac (a few on the Pi) while the obvious brute-force approach takes far longer than the limit.
"""
from dataclasses import dataclass

from cases import equal_value, parse_json, strip_code_fence
from code_runner import ALLOWED_IMPORTS, SQL_IMPORTS, encode, evaluate, extract_code, matches, run_mutation, run_tests

CODE_RULES = (" Answer with one ```python code block containing the complete code. Use only the Python standard "
              "library: you may import " + ", ".join(sorted(ALLOWED_IMPORTS - {"__future__"})) + ". Do not use "
              "eval, exec, compile, open, input, getattr, setattr or vars, and do not print anything.")
TESTGEN_RULES = (' Return only a JSON object {"tests": [...]} with 1 to 20 test cases, each {"args": [...], '
                 '"expected": ...}, where "args" lists the positional arguments and "expected" is the exact '
                 "return value of a correct implementation. Your test cases are run against a correct "
                 "implementation and against several subtly wrong ones: they pass only if every expected "
                 "value is right and, together, they expose every wrong implementation.")
JSON_INTEGER = ' Return only a JSON object with one key "answer" containing the answer as an integer.'
JSON_PRINTED = (' Return only a JSON object with one key "answer" containing, as a string, exactly the line '
                "the program prints.")


@dataclass(frozen=True)
class CodeCase:
    id: str
    kind: str                # generate, fix, predict, inverse, testgen
    prompt: str
    function: str = ""       # the name the answer must define (or the function under test)
    reference: str = ""      # correct code: the solution, the function to invert, or the implementation
    wrong: tuple = ()        # wrong solutions (generate, fix) or mutants (testgen)
    tests: tuple = ()        # hidden tests: (label, code, expected) or (label, code, expected, seconds)
    answer: object = None    # predict: the printed line; inverse: the target value
    program: str = ""        # predict: the program shown in the prompt
    sample: object = None    # inverse: one valid input; testgen: a reference set of test cases
    requires: tuple = ()
    imports: frozenset = ALLOWED_IMPORTS
    track: str = "code"
    difficulty: str = "hard"


def local(source, code):
    """Run trusted reference code here, in the parent, to compute an expected value."""
    namespace = {"__name__": "reference"}
    exec(source, namespace)
    exec(code, namespace)
    return namespace["result"]


def tests_from(reference, calls):
    """Hidden tests whose expected values come from running the reference: calls are (label, code) or
    (label, code, seconds); a call whose reference raises expects that exception."""
    from code_runner import Raises
    built = []
    for label, code, *seconds in calls:
        try:
            expected = local(reference, code)
        except Exception as error:  # noqa: BLE001 - the expected outcome is that exception
            expected = Raises(type(error).__name__)
        built.append((label, code, expected, *seconds))
    return tuple(built)


def generated(case_id, kind, function, prompt, reference, wrong, calls, requires=None, imports=ALLOWED_IMPORTS):
    return CodeCase(case_id, kind, prompt + CODE_RULES, function, reference, tuple(wrong),
                    tests_from(reference, calls), requires=tuple(requires or [function]), imports=imports)


# ---------------------------------------------------------------- generate

MERGE = '''def merge_intervals(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result
'''
MERGE_WRONG = (MERGE.replace("start <= result[-1][1] + 1", "start <= result[-1][1]"),  # touching ends kept apart
               MERGE.replace("max(result[-1][1], end)", "end"),                       # nested interval shrinks
               '''def merge_intervals(intervals):
    merged = [list(item) for item in intervals]
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i], merged[j]
                if a[0] <= b[1] + 1 and b[0] <= a[1] + 1:
                    merged[i] = [min(a[0], b[0]), max(a[1], b[1])]
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return sorted(merged)
''')                                                                                  # correct but quadratic
BIG_INTERVALS = ("import random\nrng = random.Random(7)\nitems = []\nfor _ in range(200000):\n"
                 "    start = rng.randrange(10 ** 7)\n    items.append([start, start + rng.randrange(40)])\n")

BUSINESS = '''import datetime


def add_business_days(start, days, holidays):
    day = datetime.date.fromisoformat(start)
    closed = {datetime.date.fromisoformat(item) for item in holidays}
    step = 1 if days > 0 else -1
    remaining = abs(days)
    while remaining:
        day += datetime.timedelta(days=step)
        if day.weekday() < 5 and day not in closed:
            remaining -= 1
    return day.isoformat()
'''
BUSINESS_WRONG = (BUSINESS.replace("    while remaining:\n        day += datetime.timedelta(days=step)\n"
                                   "        if day.weekday() < 5 and day not in closed:\n            remaining -= 1\n",
                                   "    while remaining:\n        if day.weekday() < 5 and day not in closed:\n"
                                   "            remaining -= 1\n        day += datetime.timedelta(days=step)\n"),  # counts the start day
                  BUSINESS.replace("    step = 1 if days > 0 else -1\n", "    step = 1\n"),           # ignores negative days
                  BUSINESS.replace("if day.weekday() < 5 and day not in closed:", "if day.weekday() < 5:"))  # ignores holidays

SPLIT = '''def split_amount(total_cents, weights):
    whole = sum(weights)
    shares = [total_cents * weight // whole for weight in weights]
    remainders = [total_cents * weight % whole for weight in weights]
    left = total_cents - sum(shares)
    order = sorted(range(len(weights)), key=lambda index: (-remainders[index], index))
    for index in order[:left]:
        shares[index] += 1
    return shares
'''
SPLIT_WRONG = (SPLIT.replace("key=lambda index: (-remainders[index], index)",
                             "key=lambda index: (-remainders[index], -index)"),             # ties go to later shares
               '''def split_amount(total_cents, weights):
    whole = sum(weights)
    shares = [round(total_cents * weight / whole) for weight in weights]
    shares[-1] += total_cents - sum(shares)
    return shares
''',                                                                                       # float rounding, rest to last
               '''def split_amount(total_cents, weights):
    whole = sum(weights)
    shares = [total_cents * weight // whole for weight in weights]
    shares[-1] += total_cents - sum(shares)
    return shares
''')                                                                                       # leftover cents to last

EXPRESSION = '''def evaluate_expression(text):
    tokens, index = [], 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char.isdigit():
            end = index
            while end < len(text) and text[end].isdigit():
                end += 1
            tokens.append(int(text[index:end]))
            index = end
        elif char in "+-*()":
            tokens.append(char)
            index += 1
        else:
            raise ValueError("unexpected character")
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        position += 1
        return tokens[position - 1]

    def expression():
        value = term()
        while peek() in ("+", "-"):
            operator = take()
            right = term()
            value = value + right if operator == "+" else value - right
        return value

    def term():
        value = factor()
        while peek() == "*":
            take()
            value *= factor()
        return value

    def factor():
        token = peek()
        if token == "-":
            take()
            return -factor()
        if token == "+":
            take()
            return factor()
        if token == "(":
            take()
            value = expression()
            if peek() != ")":
                raise ValueError("missing )")
            take()
            return value
        if type(token) is int:
            take()
            return token
        raise ValueError("unexpected token")

    value = expression()
    if position != len(tokens):
        raise ValueError("unexpected trailing input")
    return value
'''
EXPRESSION_WRONG = (EXPRESSION.replace("            right = term()\n            value = value + right if operator == \"+\" else value - right\n",
                                       "            right = expression()\n            value = value + right if operator == \"+\" else value - right\n"),  # right-associative minus
                    EXPRESSION.replace('    if position != len(tokens):\n        raise ValueError("unexpected trailing input")\n', ""),  # accepts "1 2"
                    EXPRESSION.replace('        if token == "-":\n            take()\n            return -factor()\n', ""))  # no unary minus

TOPOLOGICAL = '''import heapq


def task_order(tasks, dependencies):
    after = {task: [] for task in tasks}
    waiting = {task: 0 for task in tasks}
    for before, later in dependencies:
        after[before].append(later)
        waiting[later] += 1
    ready = [task for task in tasks if waiting[task] == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        task = heapq.heappop(ready)
        order.append(task)
        for later in after[task]:
            waiting[later] -= 1
            if waiting[later] == 0:
                heapq.heappush(ready, later)
    if len(order) != len(tasks):
        raise ValueError("cycle")
    return order
'''
TOPOLOGICAL_WRONG = ('''from collections import deque


def task_order(tasks, dependencies):
    after = {task: [] for task in tasks}
    waiting = {task: 0 for task in tasks}
    for before, later in dependencies:
        after[before].append(later)
        waiting[later] += 1
    ready = deque(sorted(task for task in tasks if waiting[task] == 0))
    order = []
    while ready:
        task = ready.popleft()
        order.append(task)
        for later in after[task]:
            waiting[later] -= 1
            if waiting[later] == 0:
                ready.append(later)
    if len(order) != len(tasks):
        raise ValueError("cycle")
    return order
''',                                                                     # first-in first-out, not alphabetical
                     TOPOLOGICAL.replace('    if len(order) != len(tasks):\n        raise ValueError("cycle")\n', ""),  # no cycle check
                     '''def task_order(tasks, dependencies):
    waiting = {task: 0 for task in tasks}
    for before, later in dependencies:
        waiting[later] += 1
    done, order = set(), []
    while len(order) < len(tasks):
        ready = [task for task in tasks if task not in done and waiting[task] == 0]
        if not ready:
            raise ValueError("cycle")
        task = min(ready)
        done.add(task)
        order.append(task)
        for before, later in dependencies:
            if before == task:
                waiting[later] -= 1
    return order
''')                                                                      # correct but quadratic
BIG_TASKS = ("import random\nrng = random.Random(11)\nnames = [f't{index:06d}' for index in range(60000)]\n"
             "rng.shuffle(names)\ndeps = [(names[i], names[j]) for i, j in "
             "((rng.randrange(60000), rng.randrange(60000)) for _ in range(60000)) if i < j]\n")

SQL_SETUP = ("import sqlite3\nconn = sqlite3.connect(':memory:')\n"
             "conn.execute('CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT)')\n"
             "conn.execute('CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, placed_on TEXT, "
             "status TEXT, amount_cents INTEGER)')\n")
SQL_REFERENCE = '''QUERY = """
WITH totals AS (
    SELECT c.city, c.name, SUM(o.amount_cents) AS total
    FROM customers AS c JOIN orders AS o ON o.customer_id = c.id
    WHERE o.status = 'paid' AND o.placed_on >= '2026-01-01' AND o.placed_on < '2027-01-01'
    GROUP BY c.id
), ranked AS (
    SELECT city, name, total,
           ROW_NUMBER() OVER (PARTITION BY city ORDER BY total DESC, name ASC) AS place
    FROM totals
)
SELECT city, name, total FROM ranked WHERE place = 1 ORDER BY city
"""
'''
SQL_WRONG = (SQL_REFERENCE.replace("o.status = 'paid'", "o.status != 'cancelled'"),   # counts refunded orders
             SQL_REFERENCE.replace("ORDER BY total DESC, name ASC", "ORDER BY total DESC, name DESC"),  # wrong tie-break
             SQL_REFERENCE.replace("SUM(o.amount_cents)", "MAX(o.amount_cents)"),       # largest order, not total
             SQL_REFERENCE.replace(" AND o.placed_on < '2027-01-01'", ""))             # includes 2027 orders


def sql_call(label, customers, orders):
    rows = (f"conn.executemany('INSERT INTO customers VALUES (?, ?, ?)', {customers!r})\n"
            f"conn.executemany('INSERT INTO orders VALUES (?, ?, ?, ?, ?)', {orders!r})\n")
    return (label, SQL_SETUP + rows + "result = [list(row) for row in conn.execute(QUERY).fetchall()]")


SQL_EXAMPLE_CUSTOMERS = [(1, "Ana", "Perth"), (2, "Ben", "Perth"), (3, "Cy", "Hobart")]
SQL_EXAMPLE_ORDERS = [(1, 1, "2026-03-02", "paid", 1500), (2, 2, "2026-04-10", "paid", 900),
                      (3, 2, "2026-05-01", "paid", 700), (4, 3, "2025-12-30", "paid", 5000),
                      (5, 3, "2026-01-03", "refunded", 800), (6, 3, "2026-02-14", "paid", 300)]

GENERATE = [
    generated("code-merge-intervals", "generate", "merge_intervals",
              "Write a function merge_intervals(intervals) that takes a list of integer intervals [start, end] "
              "(inclusive, start <= end, in any order) and returns a new list of merged intervals sorted by start. "
              "Merge intervals that overlap or that touch with no integer between them, so [1, 3] and [4, 6] "
              "become [1, 6]. Do not modify the input. It must handle 200,000 intervals in well under a second. "
              "Example: merge_intervals([[8, 10], [1, 3], [2, 6], [15, 18]]) returns [[1, 6], [8, 10], [15, 18]].",
              MERGE, MERGE_WRONG,
              [("example", "result = merge_intervals([[8, 10], [1, 3], [2, 6], [15, 18]])"),
               ("empty", "result = merge_intervals([])"),
               ("touching", "result = merge_intervals([[4, 6], [1, 3], [7, 7], [9, 9]])"),
               ("nested", "result = [merge_intervals([[1, 10], [2, 3], [4, 12], [14, 15]]), merge_intervals([[1, 10], [2, 3], [6, 7]])]"),
               ("negative", "result = merge_intervals([[-5, -3], [-2, 0], [2, 2]])"),
               ("input unchanged", "data = [[1, 3], [2, 4]]\nmerge_intervals(data)\nresult = data"),
               ("large", BIG_INTERVALS + "merged = merge_intervals(items)\nresult = [len(merged), merged[::5000]]", 5.0)]),
    generated("code-business-days", "generate", "add_business_days",
              "Write a function add_business_days(start, days, holidays) where start is an ISO date string "
              '("YYYY-MM-DD"), days is an integer (possibly zero or negative) and holidays is a list of ISO date '
              "strings. Business days are Monday to Friday, except holidays. Move from start by the given number "
              "of business days (forward if positive, backward if negative), without counting start itself; with "
              "days = 0 return start unchanged, even if it is not a business day. Return an ISO date string. "
              'Example: add_business_days("2026-11-27", 1, []) returns "2026-11-30" (Friday to Monday).',
              BUSINESS, BUSINESS_WRONG,
              [("example", "result = add_business_days('2026-11-27', 1, [])"),
               ("holiday", "result = add_business_days('2026-11-27', 1, ['2026-11-30'])"),
               ("year end", "result = add_business_days('2026-12-24', 3, ['2026-12-25', '2026-12-28', '2027-01-01'])"),
               ("backward", "result = add_business_days('2026-11-30', -1, [])"),
               ("backward holiday", "result = add_business_days('2026-12-29', -2, ['2026-12-25', '2026-12-28'])"),
               ("zero on a weekend", "result = add_business_days('2026-11-28', 0, ['2026-11-28'])"),
               ("weekend holiday", "result = add_business_days('2026-11-27', 2, ['2026-11-28', '2026-11-29'])"),
               ("long", "result = add_business_days('2026-01-01', 260, ['2026-12-25', '2026-01-26', '2026-04-03'])")]),
    generated("code-split-amount", "generate", "split_amount",
              "Write a function split_amount(total_cents, weights) that splits a non-negative integer amount of "
              "cents into integer shares proportional to a list of positive integer weights. The shares must sum "
              "exactly to total_cents. Give each share the whole part of its exact proportional amount, then hand "
              "the remaining cents out one at a time, largest fractional remainder first; when remainders are "
              "equal, the earlier share gets the cent first. Amounts can be far larger than 2**53, so work in "
              "exact integers. Example: split_amount(100, [1, 1, 1]) returns [34, 33, 33].",
              SPLIT, SPLIT_WRONG,
              [("example", "result = split_amount(100, [1, 1, 1])"),
               ("remainders", "result = split_amount(1000, [1, 2, 3])"),
               ("tie", "result = split_amount(5, [3, 3])"),
               ("zero", "result = split_amount(0, [1, 2])"),
               ("uneven", "result = split_amount(7, [2, 1, 4, 3])"),
               ("big", "result = split_amount(10 ** 18 + 1, [1, 1, 1])"),
               ("many", "result = split_amount(997, [5, 3, 3, 1, 7, 2, 2])")]),
    generated("code-expression", "generate", "evaluate_expression",
              "Write a function evaluate_expression(text) that evaluates an integer arithmetic expression with "
              "non-negative integer literals, binary +, - and *, unary minus and plus, and parentheses; whitespace between tokens is ignored. "
              "* binds tighter than + and -, which are left-associative; a unary sign applies to the factor right "
              "after it, so 2*-3 is -6 and -2*3 is -6, and signs may repeat (--2 is 2). Return the integer value. Raise ValueError for anything "
              "malformed: an empty expression, a missing operand or parenthesis, two numbers in a row, or any "
              "other character. Example: evaluate_expression(\"10 - 3 - 2\") returns 5.",
              EXPRESSION, EXPRESSION_WRONG,
              [("example", "result = evaluate_expression('10 - 3 - 2')"),
               ("precedence", "result = evaluate_expression('2 + 3 * 4 - 5')"),
               ("unary", "result = evaluate_expression('-(2 + 3) * 4 - -2')"),
               ("unary after operator", "result = evaluate_expression('2*-3')"),
               ("spaces and parentheses", "result = evaluate_expression(' 7 *( 1 + 2 ) - (4-(1-1)) ')"),
               ("big", "result = evaluate_expression('123456789 * 987654321 - 1')"),
               ("two numbers", "result = evaluate_expression('1 2')"),
               ("missing parenthesis", "result = evaluate_expression('(1 + 2')"),
               ("empty", "result = evaluate_expression('   ')"),
               ("dangling operator", "result = evaluate_expression('4 *')"),
               ("other character", "result = evaluate_expression('2 / 1')")]),
    generated("code-task-order", "generate", "task_order",
              "Write a function task_order(tasks, dependencies) where tasks is a list of distinct task names and "
              "dependencies is a list of [before, after] pairs of names from tasks (a pair may repeat). Return the tasks in an order "
              "where every task comes after all tasks it depends on; whenever several tasks are ready, take the "
              "alphabetically smallest first. Raise ValueError if the dependencies contain a cycle. It must handle "
              "60,000 tasks in well under a second. Example: task_order([\"c\", \"b\", \"a\"], [[\"c\", \"a\"]]) "
              'returns ["b", "c", "a"].',
              TOPOLOGICAL, TOPOLOGICAL_WRONG,
              [("example", "result = task_order(['c', 'b', 'a'], [['c', 'a']])"),
               ("no dependencies", "result = task_order(['b', 'a', 'c'], [])"),
               ("ready later", "result = task_order(['d', 'a', 'z', 'b'], [['z', 'b'], ['a', 'z']])"),
               ("newly ready first", "result = task_order(['a', 'c', 'b'], [['a', 'b']])"),
               ("diamond", "result = task_order(['w', 'x', 'y', 'z'], [['w', 'y'], ['w', 'x'], ['x', 'z'], ['y', 'z'], ['w', 'y']])"),
               ("cycle", "result = task_order(['a', 'b', 'c'], [['a', 'b'], ['b', 'c'], ['c', 'b']])"),
               ("self", "result = task_order(['a'], [['a', 'a']])"),
               ("large", BIG_TASKS + "order = task_order(names, deps)\nresult = [len(order), order[::997]]", 5.0)]),
    generated("code-sql-top-customer", "generate", "QUERY",
              "A SQLite database has tables customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT) and "
              "orders(id INTEGER PRIMARY KEY, customer_id INTEGER, placed_on TEXT, status TEXT, amount_cents "
              "INTEGER), where placed_on is an ISO date and status is 'paid', 'refunded' or 'cancelled'. Write a "
              "Python module that defines QUERY, one SQL string that returns, for each city, the customer with the "
              "highest total of paid orders placed in 2026, as rows (city, name, total_cents) sorted by city. "
              "Only paid orders from 2026 count; customers with none are left out, and so are cities with none. "
              "Customer names are unique. If two customers in a city tie, take the alphabetically first name. Example: with customers "
              f"{SQL_EXAMPLE_CUSTOMERS} and orders {SQL_EXAMPLE_ORDERS} (id, customer_id, placed_on, status, "
              "amount_cents), the rows are ('Hobart', 'Cy', 300) and ('Perth', 'Ben', 1600).",
              SQL_REFERENCE, SQL_WRONG,
              [sql_call("example", SQL_EXAMPLE_CUSTOMERS, SQL_EXAMPLE_ORDERS),
               sql_call("tie and refunds", [(1, "Zoe", "Perth"), (2, "Abe", "Perth"), (3, "Mo", "Perth"), (4, "Lu", "Darwin")],
                        [(1, 1, "2026-02-01", "paid", 500), (2, 2, "2026-03-01", "paid", 200), (3, 2, "2026-03-05", "paid", 300),
                         (4, 3, "2026-06-01", "refunded", 9000), (5, 3, "2026-06-02", "paid", 100),
                         (6, 4, "2026-12-31", "paid", 50), (7, 4, "2027-01-01", "paid", 70000)]),
               sql_call("many small orders", [(1, "Ida", "Cairns"), (2, "Jo", "Cairns"), (3, "Kai", "Broome")],
                        [(1, 1, "2026-01-01", "paid", 900), (2, 2, "2026-01-02", "paid", 400), (3, 2, "2026-01-03", "paid", 400),
                         (4, 2, "2026-01-04", "paid", 400), (5, 3, "2026-05-05", "cancelled", 800),
                         (6, 3, "2025-05-05", "paid", 800)])],
              requires=["QUERY"], imports=ALLOWED_IMPORTS | SQL_IMPORTS),
]


# ---------------------------------------------------------------- fix


def fixing(case_id, function, task, buggy, failure, reference, wrong, calls):
    prompt = (f"{task} This implementation has a bug:\n\n```python\n{buggy}```\n\n{failure} Find and fix every bug, "
              "and return the complete corrected code.")
    return generated(case_id, "fix", function, prompt, reference, (buggy, *wrong), calls)


LIMITER = '''from collections import deque


class RateLimiter:
    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self.times = deque()

    def allow(self, t):
        while self.times and self.times[0] <= t - self.window:
            self.times.popleft()
        if len(self.times) < self.limit:
            self.times.append(t)
            return True
        return False
'''
LIMITER_BUGGY = '''from collections import deque


class RateLimiter:
    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self.times = deque()

    def allow(self, t):
        while self.times and self.times[0] < t - self.window:
            self.times.popleft()
        self.times.append(t)
        return len(self.times) <= self.limit
'''
LIMITER_WRONG = (LIMITER_BUGGY.replace("self.times[0] < t - self.window", "self.times[0] <= t - self.window"),  # boundary only
                 LIMITER.replace("self.times[0] <= t - self.window", "self.times[0] < t - self.window"))  # rejection only


def limiter_call(label, limit, window, times):
    return (label, f"limiter = RateLimiter({limit}, {window})\nresult = [limiter.allow(t) for t in {times!r}]")


TAG = '''import re

TAG = re.compile(r"[a-z](?!.*--)[a-z0-9-]*[a-z0-9]")


def is_valid_tag(tag):
    return 3 <= len(tag) <= 20 and TAG.fullmatch(tag) is not None
'''
TAG_BUGGY = '''import re


def is_valid_tag(tag):
    return re.match(r"[a-z][a-z0-9-]{1,18}[a-z0-9]", tag) is not None
'''
TAG_WRONG = (TAG_BUGGY.replace('r"[a-z][a-z0-9-]{1,18}[a-z0-9]"', 'r"[a-z][a-z0-9-]{1,18}[a-z0-9]$"'),  # $ allows a final newline
             TAG.replace("(?!.*--)", ""))                                                             # allows "--"

SEARCH = '''def first_index(items, target):
    low, high = 0, len(items)
    while low < high:
        middle = (low + high) // 2
        if items[middle] < target:
            low = middle + 1
        else:
            high = middle
    return low if low < len(items) and items[low] == target else -1
'''
SEARCH_BUGGY = '''def first_index(items, target):
    low, high = 0, len(items) - 1
    while low <= high:
        middle = (low + high) // 2
        if items[middle] == target:
            return middle
        if items[middle] < target:
            low = middle + 1
        else:
            high = middle - 1
    return -1
'''
SEARCH_WRONG = ('''def first_index(items, target):
    return items.index(target) if target in items else -1
''',                                                                                       # correct but linear
                SEARCH.replace("return low if low < len(items) and items[low] == target else -1", "return low"))  # no check
BIG_SEARCH = ("items = [value // 3 for value in range(600000)]\n"
              "result = [first_index(items, target) for target in range(-1, 200002, 7)]\n")

CSV = '''def split_csv_line(line):
    fields, current, index, quoted = [], [], 0, False
    while index < len(line):
        char = line[index]
        if quoted:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
        elif char == ",":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    if quoted:
        raise ValueError("unterminated quote")
    fields.append("".join(current))
    return fields
'''
CSV_BUGGY = '''def split_csv_line(line):
    fields, current, quoted = [], [], False
    for char in line:
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields
'''
CSV_WRONG = (CSV.replace('    if quoted:\n        raise ValueError("unterminated quote")\n', ""),  # no error for an open quote
             '''def split_csv_line(line):
    if '"' not in line:
        return line.split(",")
    fields, current, index, quoted = [], [], 0, False
    while index < len(line):
        char = line[index]
        if char == '"' and quoted and index + 1 < len(line) and line[index + 1] == '"':
            current.append('"')
            index += 2
            continue
        if char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    fields.append("".join(current))
    return fields
''')                                                                                         # "" fixed, no error

LRU = '''from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        if key not in self.data:
            return -1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        if key in self.data:
            self.data.move_to_end(key)
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
'''
LRU_BUGGY = '''from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()

    def get(self, key):
        return self.data.get(key, -1)

    def put(self, key, value):
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
'''
LRU_WRONG = (LRU.replace("        if key in self.data:\n            self.data.move_to_end(key)\n        self.data[key] = value",
                         "        self.data[key] = value"),                                   # put doesn't refresh
             LRU.replace("self.data.popitem(last=False)", "self.data.popitem()"))           # evicts the newest


def lru_call(label, capacity, operations):
    lines = [f"cache = LRUCache({capacity})", "result = []"]
    for operation in operations:
        if operation[0] == "put":
            lines.append(f"cache.put({operation[1]!r}, {operation[2]!r})")
        else:
            lines.append(f"result.append(cache.get({operation[1]!r}))")
    return (label, "\n".join(lines))


FIX = [
    fixing("code-fix-rate-limiter", "RateLimiter",
           "RateLimiter(limit, window) allows at most limit calls in any time window: allow(t), called with "
           "non-decreasing times t, returns True and records the call if fewer than limit allowed calls have "
           "times in (t - window, t], and otherwise returns False. Rejected calls are not recorded.",
           LIMITER_BUGGY,
           "With RateLimiter(2, 10), the calls allow(0), allow(5), allow(10) should return True, True, True "
           "(the call at 0 is outside (0, 10]), but the third returns False.",
           LIMITER, LIMITER_WRONG,
           [limiter_call("example", 2, 10, [0, 5, 10]),
            limiter_call("rejections", 2, 10, [0, 1, 2, 3, 10, 11, 12]),
            limiter_call("burst", 3, 5, [0, 0, 0, 0, 4, 5, 5, 9, 10, 10, 10]),
            limiter_call("one per window", 1, 1, [0, 0, 1, 1, 2, 3, 3])]),
    fixing("code-fix-tag", "is_valid_tag",
           "is_valid_tag(tag) should accept a tag only if it is 3 to 20 characters long, uses only lowercase "
           "ASCII letters, digits and hyphens, starts with a letter, ends with a letter or digit, and never has "
           "two hyphens in a row.",
           TAG_BUGGY, 'is_valid_tag("abc!") returns True, but the tag contains "!".',
           TAG, TAG_WRONG,
           [("example", "result = is_valid_tag('abc!')"),
            ("good", "result = [is_valid_tag(tag) for tag in ['abc', 'a-b-c', 'x9', 'node-42', 'a' * 20]]"),
            ("bad", "result = [is_valid_tag(tag) for tag in ['ab', 'a' * 21, 'a--b', 'abc-', '1abc', 'Abc', 'ab c']]"),
            ("newline", "result = [is_valid_tag('abc\\n'), is_valid_tag('ab-\\n')]"),
            ("hyphens", "result = [is_valid_tag('a-b--c'), is_valid_tag('a-b-c-d'), is_valid_tag('-abc')]")]),
    fixing("code-fix-first-index", "first_index",
           "first_index(items, target) takes a list sorted in ascending order (it may contain repeats) and "
           "returns the index of the first item equal to target, or -1 if there is none. It must take "
           "logarithmic time: it is called many times on lists of hundreds of thousands of items.",
           SEARCH_BUGGY, "first_index([1, 2, 2, 2, 3], 2) returns 2, but the first 2 is at index 1.",
           SEARCH, SEARCH_WRONG,
           [("example", "result = first_index([1, 2, 2, 2, 3], 2)"),
            ("ends", "result = [first_index([4, 4, 4], 4), first_index([1, 3], 3), first_index([1, 3], 2)]"),
            ("missing", "result = [first_index([], 1), first_index([1, 2], 0), first_index([1, 2], 5)]"),
            ("large", BIG_SEARCH, 5.0)]),
    fixing("code-fix-csv", "split_csv_line",
           "split_csv_line(line) splits one CSV line into fields at commas. A field may be wrapped in double "
           'quotes, inside which commas are ordinary characters and two double quotes ("") stand for one. The '
           "quotes themselves are not part of the field, and spaces are kept. Quotes appear only around a whole "
           "field, never inside an unquoted one. Raise ValueError if a quote is "
           "left open at the end of the line.",
           CSV_BUGGY, 'split_csv_line(\'a,"say ""hi""",c\') returns ["a", "say hi", "c"] instead of '
           '["a", "say \\"hi\\"", "c"].',
           CSV, CSV_WRONG,
           [("example", "result = split_csv_line('a,\"say \"\"hi\"\"\",c')"),
            ("plain", "result = split_csv_line(' a , b ,c')"),
            ("empty fields", "result = [split_csv_line(''), split_csv_line(',,'), split_csv_line('\"\"')]"),
            ("quoted comma", "result = split_csv_line('\"x,y\",\"\",z')"),
            ("open quote", "result = split_csv_line('a,\"b')"),
            ("open after escape", "result = split_csv_line('\"\"\"')")]),
    fixing("code-fix-lru", "LRUCache",
           "LRUCache(capacity) keeps at most capacity entries: get(key) returns the value, or -1 if absent, and "
           "counts as a use; put(key, value) inserts or updates the entry, which also counts as a use, and then "
           "removes the least recently used entry if there are more than capacity.",
           LRU_BUGGY, "With capacity 2: put(1, 1), put(2, 2), get(1), put(3, 3), get(2) should return -1 for the "
           "last call, since 2 was used least recently, but it returns 2.",
           LRU, LRU_WRONG,
           [lru_call("example", 2, [("put", 1, 1), ("put", 2, 2), ("get", 1), ("put", 3, 3), ("get", 2), ("get", 1), ("get", 3)]),
            lru_call("update refreshes", 2, [("put", "a", 1), ("put", "b", 2), ("put", "a", 3), ("put", "c", 4),
                                              ("get", "b"), ("get", "a"), ("get", "c")]),
            lru_call("capacity one", 1, [("put", 1, 1), ("put", 2, 2), ("get", 1), ("get", 2), ("put", 2, 5), ("get", 2)])]),
]


# ---------------------------------------------------------------- predict and inverse

PROGRAMS = (
    ("code-predict-closures", '''def make():
    keep = [lambda x, i=i: x * i for i in range(3)]
    late = [lambda x: x * i for i in range(3)]
    return keep + late

i = 10
print(sum(f(2) for f in make()))
'''),
    ("code-predict-default", '''def add(item, bucket=[]):
    bucket.append(item)
    return bucket

a = add(1)
b = add(2, [])
c = add(3)
print(len(a) * 100 + len(b) * 10 + len(c), a is c)
'''),
    ("code-predict-iterators", '''numbers = iter(range(11))
pairs = list(zip(numbers, numbers))
rest = list(numbers)
print(len(pairs), sum(a * b for a, b in pairs), rest)
'''),
    ("code-predict-arithmetic", '''values = [-7 // 2, -7 % 3, int(-2.7), round(2.5), round(-3.5), 7 // -2 * -1]
print(sum(values), values[-1], 0.1 + 0.2 == 0.3)
'''),
)


def printed(program):
    """What a program prints, by running it here (it is trusted suite code)."""
    import contextlib
    import io
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(program, {"__name__": "__main__"})
    return buffer.getvalue().rstrip("\n")


PREDICT = [CodeCase(case_id, "predict", "What does this Python 3 program print?\n\n```python\n" + program + "```\n"
                    + JSON_PRINTED, program=program, answer=printed(program)) for case_id, program in PROGRAMS]

INVERSE_FUNCTION = '''def f(n):
    digits = [int(d) for d in str(n)]
    total = 0
    for index, digit in enumerate(digits):
        if index % 2 == 0:
            total += digit * (index + 1)
        else:
            total -= digit
    return total
'''
INVERSE = CodeCase("code-inverse-digits", "inverse",
                   "Here is a Python function:\n\n```python\n" + INVERSE_FUNCTION + "```\n\nGive an even "
                   "three-digit integer n (100 <= n <= 999) for which f(n) returns 17." + JSON_INTEGER,
                   function="f", reference=INVERSE_FUNCTION, answer=17, sample=504,
                   tests=(("check", "n = DATA\nresult = f(n) if type(n) is int and 100 <= n <= 999 and n % 2 == 0 else None"),))


# ---------------------------------------------------------------- testgen

VERSIONS = '''def compare_versions(a, b):
    def parse(version):
        version = version.split("+", 1)[0]
        core, _, pre = version.partition("-")
        return [int(part) for part in core.split(".")], pre.split(".") if pre else []

    def sign(x, y):
        return (x > y) - (x < y)

    (core_a, pre_a), (core_b, pre_b) = parse(a), parse(b)
    if core_a != core_b:
        return sign(core_a, core_b)
    if not pre_a or not pre_b:
        return sign(not pre_a, not pre_b)
    for x, y in zip(pre_a, pre_b):
        if x == y:
            continue
        x_number, y_number = x.isdigit(), y.isdigit()
        if x_number and y_number:
            return sign(int(x), int(y))
        if x_number != y_number:
            return -1 if x_number else 1
        return sign(x, y)
    return sign(len(pre_a), len(pre_b))
'''
VERSIONS_MUTANTS = (VERSIONS.replace("[int(part) for part in core.split(\".\")]", "core.split(\".\")"),   # "10" < "9"
                    VERSIONS.replace("sign(not pre_a, not pre_b)", "sign(not pre_b, not pre_a)"),     # release before pre-release
                    VERSIONS.replace("        if x_number and y_number:\n            return sign(int(x), int(y))\n", ""),  # "11" < "2"
                    VERSIONS.replace("return -1 if x_number else 1", "return 1 if x_number else -1"),  # numbers after words
                    VERSIONS.replace('        version = version.split("+", 1)[0]\n', ""),             # build metadata not ignored
                    VERSIONS.replace("sign(len(pre_a), len(pre_b))", "sign(len(pre_b), len(pre_a))"))  # longer set first
VERSIONS_SAMPLE = [{"args": a, "expected": e} for a, e in (
    (["1.2.3", "1.2.3"], 0), (["1.10.0", "1.9.0"], 1), (["1.0.0-alpha", "1.0.0"], -1), (["1.0.0", "1.0.0-rc.1"], 1),
    (["1.0.0-rc.11", "1.0.0-rc.2"], 1), (["1.0.0-1", "1.0.0-alpha"], -1), (["1.0.0+build.2", "1.0.0+build.1"], 0),
    (["1.0.0-alpha.1", "1.0.0-alpha"], 1), (["1.0.0-alpha", "1.0.0-beta"], -1))]

ROMAN = '''def roman_to_int(numeral):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for index, char in enumerate(numeral):
        value = values[char]
        if index + 1 < len(numeral) and values[numeral[index + 1]] > value:
            total -= value
        else:
            total += value
    return total
'''
ROMAN_MUTANTS = (ROMAN.replace("values[numeral[index + 1]] > value", "values[numeral[index + 1]] >= value"),  # "II" is 0
                 ROMAN.replace("if index + 1 < len(numeral) and", 'if char == "I" and index + 1 < len(numeral) and'),  # only I subtracts
                 ROMAN.replace("            total -= value\n", "            total += value\n"),            # never subtracts
                 ROMAN.replace("        else:\n            total += value\n", "        elif index + 1 < len(numeral):\n            total += value\n"),  # drops the last numeral
                 ROMAN.replace('"L": 50, "C": 100, "D": 500', '"L": 500, "C": 100, "D": 50'))             # L and D swapped
ROMAN_SAMPLE = [{"args": [n], "expected": e} for n, e in (("III", 3), ("IV", 4), ("XC", 90), ("L", 50), ("D", 500),
                                                          ("MCMXCIV", 1994), ("I", 1))]

PATH = '''def normalize_path(path):
    absolute = path.startswith("/")
    parts = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            elif not absolute:
                parts.append("..")
            continue
        parts.append(part)
    joined = "/".join(parts)
    if absolute:
        return "/" + joined
    return joined or "."
'''
PATH_MUTANTS = (PATH.replace("            elif not absolute:\n", "            else:\n"),                      # "/.." stays
                PATH.replace('            elif not absolute:\n                parts.append("..")\n', ""),   # leading ".." dropped
                PATH.replace('    return joined or "."\n', "    return joined\n"),                           # "" instead of "."
                PATH.replace('if part in ("", "."):', 'if part == "":'),                                   # "." kept
                PATH.replace('if parts and parts[-1] != "..":', "if parts:"),                              # ".." cancels ".."
                PATH.replace('if part in ("", "."):', 'if part == ".":'))                                  # "//" kept
PATH_SAMPLE = [{"args": [p], "expected": e} for p, e in (
    ("/a/b/../c/", "/a/c"), ("/../x", "/x"), ("../a", "../a"), ("a/..", "."), ("./a/./b", "a/b"),
    ("../../a", "../../a"), ("a//b", "a/b"), ("/", "/"))]

WRAP = '''def word_wrap(text, width):
    lines, current = [], ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
'''
WRAP_MUTANTS = (WRAP.replace("len(current) + 1 + len(word) <= width", "len(current) + 1 + len(word) < width"),  # strict
                WRAP.replace("len(current) + 1 + len(word) <= width", "len(current) + len(word) <= width"),     # forgets the space
                WRAP.replace("text.split()", 'text.split(" ")'),                                               # keeps empty words
                WRAP.replace("    return lines\n", '    return lines or [""]\n'),                                # blank gives [""]
                WRAP.replace("    if current:\n        lines.append(current)\n", ""),                           # drops the last line
                '''def word_wrap(text, width):
    lines, current = [], ""
    for word in text.split():
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
''')                                                                                                        # breaks long words
WRAP_SAMPLE = [{"args": a, "expected": e} for a, e in (
    (["the quick brown fox", 9], ["the quick", "brown fox"]), (["ab cd", 4], ["ab", "cd"]),
    (["a  b", 3], ["a b"]), (["   ", 5], []), (["extraordinary day", 5], ["extraordinary", "day"]),
    (["one", 10], ["one"]))]


def testgen(case_id, function, description, reference, mutants, sample):
    return CodeCase(case_id, "testgen", f"Write test cases for this function, without writing it: {description}"
                    + TESTGEN_RULES, function, reference, tuple(mutants), sample=sample)


TESTGEN = [
    testgen("code-tests-versions", "compare_versions",
            "compare_versions(a, b) compares two semantic version strings and returns -1, 0 or 1. A version is "
            "MAJOR.MINOR.PATCH with non-negative integers, optionally followed by -PRERELEASE (dot-separated "
            "identifiers) and then +BUILD, which is ignored. Cores compare numerically. A version with a "
            "pre-release is lower than the same version without one. Pre-release identifiers compare left to "
            "right: numeric identifiers numerically, others in ASCII order, and a numeric identifier is lower "
            "than a non-numeric one; if all shared identifiers are equal, the version with fewer identifiers is "
            "lower.", VERSIONS, VERSIONS_MUTANTS, VERSIONS_SAMPLE),
    testgen("code-tests-roman", "roman_to_int",
            "roman_to_int(numeral) returns the integer value (1 to 3999) of a valid Roman numeral written with "
            "I, V, X, L, C, D and M, including the subtractive forms such as IV, IX, XL, XC, CD and CM.",
            ROMAN, ROMAN_MUTANTS, ROMAN_SAMPLE),
    testgen("code-tests-paths", "normalize_path",
            "normalize_path(path) normalizes a POSIX-style path string without touching the file system: it "
            "removes empty and '.' components, applies '..' to the previous component, keeps a single leading '/' for "
            "absolute paths, however many slashes they start with (and '..' at the root stays at the root), keeps leading '..' components of relative "
            "paths, and returns '.' for a relative path that becomes empty.",
            PATH, PATH_MUTANTS, PATH_SAMPLE),
    testgen("code-tests-wrap", "word_wrap",
            "word_wrap(text, width) splits text into words at any whitespace and returns a list of lines, filling "
            "each line greedily with words separated by single spaces as long as the line is at most width "
            "characters; a word longer than width gets a line of its own and is not broken. Blank text gives an "
            "empty list.", WRAP, WRAP_MUTANTS, WRAP_SAMPLE),
]

CODE_CASES = [*GENERATE, *FIX, *PREDICT, INVERSE, *TESTGEN]


# ---------------------------------------------------------------- scoring


def typed_answer(content):
    """The value of a JSON {"answer": ...} reply, or None and a status."""
    try:
        reply = parse_json(strip_code_fence(content))
    except (ValueError, RecursionError):
        return None, "invalid_json"
    if not isinstance(reply, dict) or reply.keys() != {"answer"}:
        return None, "wrong_answer"
    return reply["answer"], None


def score_code(case, content):
    """The status of one model reply to a code case."""
    if case.kind in ("generate", "fix"):
        return run_tests(extract_code(content), case.tests, case.requires, case.imports).status
    if case.kind == "predict":
        value, status = typed_answer(content)
        if status:
            return status
        return "pass" if equal_value(value, case.answer) else "wrong_answer"
    if case.kind == "inverse":
        value, status = typed_answer(content)
        if status:
            return status
        if type(value) is not int or abs(value) > 10 ** 6:
            return "wrong_answer"
        problem, outcomes = evaluate(case.reference, [(label, code) for label, code in case.tests], [case.function],
                                     untrusted=False, data=value)
        if problem or not outcomes or outcomes[0][0] != "value":
            return "wrong_answer"
        return "pass" if matches(outcomes[0][1], encode(case.answer)) else "wrong_answer"
    if case.kind == "testgen":
        try:
            answer = parse_json(strip_code_fence(content))
        except (ValueError, RecursionError):
            return "invalid_json"
        return run_mutation(answer, case.reference, case.wrong, case.function).status
    raise ValueError(f"unknown code case kind {case.kind}")
