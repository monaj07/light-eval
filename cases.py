"""Versioned fixtures and deterministic scoring for Light Eval."""
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.4.6"
TRACKS = ("arithmetic", "structured", "agent", "vision", "context")
DEFAULT_TOOL_NAMES = ("calculate", "find_order", "read_order", "set_indicator")


@dataclass(frozen=True)
class Case:
    id: str
    track: str
    prompt: str
    answer: object
    image: str | None = None
    calls: tuple = ()
    state: str | None = None
    tolerance: float = 0.0
    difficulty: str = "control"
    initial_state: str = "red"
    tool_names: tuple = DEFAULT_TOOL_NAMES

    @property
    def tools(self):
        return [item for item in TOOLS if item["function"]["name"] in self.tool_names]

    @property
    def response_schema(self):
        """Require an answer object without revealing the correct answer value."""
        if self.track not in ("arithmetic", "agent", "vision", "context"):
            return None
        json_type = {bool: "boolean", int: "number", float: "number", str: "string"}
        return {"type": "object", "properties": {"answer": {"type": json_type[type(self.answer)]}},
                "required": ["answer"], "additionalProperties": False}


def tool(name, description, fields):
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": {
            "type": "object", "properties": fields, "required": list(fields),
            "additionalProperties": False,
        },
    }}


TOOLS = [
    tool("calculate", "Perform an arithmetic operation on two numbers.", {
        "operation": {"type": "string", "enum": ["add", "subtract", "multiply", "divide"]},
        "a": {"type": "number"}, "b": {"type": "number"},
    }),
    tool("find_order", "Return the order ID for a customer handle.", {"customer": {"type": "string"}}),
    tool("read_order", "Return shipping status for an order ID.", {"order_id": {"type": "string"}}),
    tool("set_indicator", "Set the simulated indicator to a color.", {
        "color": {"type": "string", "enum": ["red", "green", "off"]},
    }),
    tool("read_indicator", "Read the current color of the simulated indicator without changing it.", {}),
]
TOOL_PARAMETERS = {item["function"]["name"]: item["function"]["parameters"] for item in TOOLS}
CUSTOMERS = {"mira": "ZX-318", "ren": "BC-915"}
ORDERS = {"ZX-318": "in transit", "BC-915": "delivered"}


def dispatch(name, args, state):
    if not isinstance(name, str) or name not in TOOL_PARAMETERS:
        raise ValueError("Unknown tool")
    properties = TOOL_PARAMETERS[name]["properties"]
    if not isinstance(args, dict) or args.keys() != properties.keys():
        raise ValueError("Wrong argument keys")
    for key, constraint in properties.items():
        value = args[key]
        valid = isinstance(value, str) if constraint["type"] == "string" else (
            type(value) in (int, float) and abs(value) <= 1e12
        )
        if not valid or ("enum" in constraint and value not in constraint["enum"]):
            raise ValueError(f"Invalid argument: {key}")
    if name == "calculate":
        a, b, operation = args["a"], args["b"], args["operation"]
        if operation == "divide":
            if b == 0:
                raise ValueError("Division by zero")
            result = a / b
            if not math.isfinite(result):
                raise ValueError("Nonfinite result")
            return result
        return {"add": a + b, "subtract": a - b, "multiply": a * b}[operation]
    if name == "find_order":
        return {"order_id": CUSTOMERS.get(args["customer"])}
    if name == "read_order":
        return {"status": ORDERS.get(args["order_id"])}
    if name == "read_indicator":
        return {"indicator": state["indicator"]}
    state["indicator"] = args["color"]
    return {"indicator": state["indicator"]}


def context_case(case_id, lines, position, code):
    records = [
        f"Relay R{index:03d} was checked by zone {index % 9 + 1} and is assigned shelf S{(index * 37) % 891:03d}."
        for index in range(lines)
    ]
    records[position] = f"Relay TX-41 is assigned shelf {code}."
    prompt = "Use the dispatch log to find the shelf for relay TX-41.\n\n" + "\n".join(records)
    return Case(case_id, "context", prompt +
                '\n\nReturn only a JSON object with one key "answer" containing the shelf ID as a string.', code)


def challenge_agent_cases(json_number, json_string):
    state_tools = DEFAULT_TOOL_NAMES + ("read_indicator",)
    word_only = ' The "answer" value must contain only the resulting color word, without a sentence.'
    return [
        Case("agent-chain-arithmetic", "agent",
             "Use the calculator once for each step, in this order: multiply 37 by 19; "
             "add 86 to that result; divide that new result by 3. Report the final value." + json_number,
             263, calls=(("calculate", {"operation": "multiply", "a": 37, "b": 19}),
                         ("calculate", {"operation": "add", "a": 703, "b": 86}),
                         ("calculate", {"operation": "divide", "a": 789, "b": 3})), difficulty="challenge"),
        Case("agent-status-branch", "agent",
             "Look up customer mira and read that order. If its status is delivered, set the indicator off; "
             "otherwise set it green. Perform only these necessary calls in this order and report its new color."
             + word_only + json_string, "green",
             calls=(("find_order", {"customer": "mira"}), ("read_order", {"order_id": "ZX-318"}),
                    ("set_indicator", {"color": "green"})), state="green", difficulty="challenge"),
        Case("agent-missing-customer", "agent",
             'Look up customer sol. If the returned order_id is null, stop and return "not found" as the answer value; '
             "do not read an order or change any state. Otherwise read the order and report only its exact status."
             + json_string, "not found", calls=(("find_order", {"customer": "sol"}),), difficulty="challenge"),
        Case("agent-read-before-write", "agent",
             "Read the indicator. If it is red, set it green; otherwise leave it unchanged. "
             "Then read it again to verify the final color and report that color. Use exactly this sequence."
             + word_only + json_string, "green",
             calls=(("read_indicator", {}), ("set_indicator", {"color": "green"}), ("read_indicator", {})),
             state="green", difficulty="challenge", tool_names=state_tools),
        Case("agent-no-op-state", "agent",
             "Ensure the indicator is green with the fewest calls: read it once and set it only if it is not "
             "already green. Report the resulting color. Do not perform a redundant write or a second read."
             + word_only + json_string, "green", calls=(("read_indicator", {}),), state="green",
             difficulty="challenge", initial_state="green", tool_names=state_tools),
        Case("agent-cross-order", "agent",
             "In order, look up customer mira and read that order, then look up customer ren and read that order. "
             "If both orders are delivered, set the indicator green; otherwise set it red. "
             "Then read the indicator once to verify the result. Perform only this sequence and report the verified color."
             + word_only + json_string, "red",
             calls=(("find_order", {"customer": "mira"}), ("read_order", {"order_id": "ZX-318"}),
                    ("find_order", {"customer": "ren"}), ("read_order", {"order_id": "BC-915"}),
                    ("set_indicator", {"color": "red"}), ("read_indicator", {})), state="red",
             difficulty="challenge", tool_names=state_tools),
    ]


def challenge_context_case(size, scenario):
    """Fixed, model-independent fixtures; no runtime answer selection or tuning."""
    case_id = f"context-{size}-{scenario}"
    rng = random.Random(case_id)
    count = 16 if size == "short" else 96
    target = f"TX-{rng.randrange(20, 90)}"
    records = []
    if scenario == "revision":
        for index in range(count - 5):
            records.append(f"ASSIGN relay={target}{index % 9} revision={index + 1} state=approved shelf=S{index + 100}")
        versions = [(1, "approved"), (4, "approved"), (7, "approved"), (9, "pending"), (10, "cancelled")]
        shelves = rng.sample(range(100, 999), len(versions))
        updates = [(revision, state, f"S{shelf}") for (revision, state), shelf in zip(versions, shelves)]
        records += [f"ASSIGN relay={target} revision={rev} state={state} shelf={shelf}"
                    for rev, state, shelf in updates]
        answer = max((row for row in updates if row[1] == "approved"), key=lambda row: row[0])[2]
        question = (f"For the exact relay ID {target}, find the shelf in its highest numeric revision whose "
                    "state is approved. Ignore pending and cancelled revisions. Text order is not revision order.")
    elif scenario == "join":
        alias = f"pump-{rng.randrange(100, 999)}"
        route = f"RT-{rng.randrange(100, 999)}"
        answer = f"S{rng.randrange(100, 999)}"
        for index in range(count - 5):
            suffix = index + 1
            if index % 3 == 0:
                records.append(f"ALIAS name={alias}{suffix} relay={target}{suffix}")
            elif index % 3 == 1:
                records.append(f"RELAY id={target}{suffix} route={route}{suffix}")
            else:
                records.append(f"ROUTE id={route}{suffix} shelf=S{rng.randrange(100, 999)}")
        other_shelf = f"S{100 + (int(answer[1:]) - 100 + 371) % 899}"
        records += [f"ALIAS name={alias} relay={target}", f"RELAY id={target} route={route}",
                    f"ROUTE id={route} shelf={answer}", f"ALIAS name={alias}0 relay=UNRELATED",
                    f"ROUTE id={route}0 shelf={other_shelf}"]
        question = (f"Find the shelf for alias {alias}. Follow ALIAS to RELAY to ROUTE, matching complete "
                    "identifiers exactly; similarly prefixed names are different identifiers.")
    elif scenario == "filter":
        entries = [("Z4", "active", 70), ("Z4", "active", 69),
                   ("Z4", "inactive", 80), ("Z40", "active", 80)]
        entries += [(rng.choice(("Z4", "Z40", "Z2")), rng.choice(("active", "inactive")), rng.randrange(55, 86))
                    for _ in range(count - len(entries))]
        records = [f"NODE id=N{index:03d} zone={zone} state={state} temp={temp}"
                   for index, (zone, state, temp) in enumerate(entries)]
        answer = sum(zone == "Z4" and state == "active" and temp >= 70 for zone, state, temp in entries)
        question = ("Count NODE records whose zone is exactly Z4, whose state is exactly active, and whose "
                    "temp is at least 70. Exclude Z40, inactive records, and temperatures below 70.")
    elif scenario == "absence":
        records = [f"ASSIGN relay={target}{index % 9} shelf=S{index + 100}" for index in range(count - 1)]
        records.append(f"COMMENT relay={target} shelf=S901 (not an assignment)")
        answer = "not found"
        question = (f"What shelf is assigned to the exact relay ID {target}? Use only ASSIGN records; "
                    'COMMENT records are not evidence. Match complete IDs exactly. If no assignment exists, answer "not found".')
    else:
        raise ValueError(f"Unknown context scenario: {scenario}")
    rng.shuffle(records)
    value_type = "number" if scenario == "filter" else "string"
    prompt = (question + "\n\nDispatch log (data records only):\n" + "\n".join(records) +
              f'\n\nReturn only a JSON object with one key "answer" containing the answer as a {value_type}.')
    return Case(case_id, "context", prompt, answer, difficulty="challenge")


def dataset():
    json_number = ' Return only a JSON object with one key "answer" containing the numerical answer.'
    json_string = ' Return only a JSON object with one key "answer" containing the answer as a string.'
    json_boolean = ' Return only a JSON object with one key "answer" containing a boolean (true or false).'
    cases = [
        Case("math-multiply", "arithmetic", "What is 173 × 29?" + json_number, 5017),
        Case("math-discount", "arithmetic", "Apply a 15% discount to $64. Give the final price." + json_number, 54.4),
        Case("math-fraction", "arithmetic", "What is 2 divided by 7, as a decimal to within 0.0001?" + json_number, 2 / 7, tolerance=.0001),
        Case("math-negative", "arithmetic", "Evaluate -18 + 4 × (7 - 12)." + json_number, -38),
        Case("math-rate", "arithmetic", "A pump transfers 135 litres in 9 minutes. How many litres in 25 minutes at the same rate?" + json_number, 375),
        Case("math-units", "arithmetic", "How many seconds are in 2 hours and 17 minutes?" + json_number, 8220),
        Case("math-decimal", "arithmetic", "What is 0.075 × 48?" + json_number, 3.6),
        Case("math-remainder", "arithmetic", "What is the remainder when 374 is divided by 17?" + json_number, 0),
        Case("json-shipment", "structured", 'Extract id (string), units (integer), and fragile (boolean) from: "Mark B-023 as fragile; send 12 units." Return only the JSON object.',
             {"id": "B-023", "units": 12, "fragile": True}),
        Case("json-status", "structured", 'Extract node (string) and connected (boolean) from: "Node M-14 is disconnected." Return only the JSON object.',
             {"node": "M-14", "connected": False}),
        Case("json-list", "structured", 'Return only a JSON array of active IDs in source order: Q1 active, Q2 retired, Q3 active, Q4 inactive.',
             ["Q1", "Q3"]),
        Case("json-sort", "structured", 'Sort the IDs ["G12", "G03", "G19"] by their numeric suffix. Return only a JSON array.',
             ["G03", "G12", "G19"]),
        Case("json-null", "structured", 'Return only a JSON object with keys "ticket" and "owner". Ticket P-42 is unassigned; represent the owner as null.',
             {"ticket": "P-42", "owner": None}),
        Case("json-empty", "structured", 'An inventory has A1 (expired), A2 (damaged), and A3 (missing). Return a JSON array of IDs marked "available".', []),
        Case("agent-calculate", "agent", "Use the calculator tool to find 173 × 29." + json_number, 5017,
             calls=(("calculate", {"operation": "multiply", "a": 173, "b": 29}),)),
        Case("agent-direct", "agent", 'Reply with only the JSON object {"answer":"ready"}. Do not call any tools.', "ready"),
        Case("agent-order", "agent", "Find Mira's order using customer handle mira, then report its shipping status. "
             'The "answer" value must be only the exact status field returned by the order tool, without a sentence.' + json_string, "in transit",
             calls=(("find_order", {"customer": "mira"}), ("read_order", {"order_id": "ZX-318"}))),
        Case("agent-second-order", "agent", "Look up customer handle ren and check that customer's order. Report its shipping status. "
             'The "answer" value must be only the exact status field returned by the order tool, without a sentence.' + json_string, "delivered",
             calls=(("find_order", {"customer": "ren"}), ("read_order", {"order_id": "BC-915"}))),
        Case("agent-indicator", "agent", "Set the simulated indicator to green, then report its new color. "
             'The "answer" value must contain only the color word, without a sentence.' + json_string, "green",
             calls=(("set_indicator", {"color": "green"}),), state="green"),
        Case("agent-off", "agent", "Switch the simulated indicator off and report its new state. "
             'The "answer" value must contain only the state word, without a sentence.' + json_string, "off",
             calls=(("set_indicator", {"color": "off"}),), state="off"),
        Case("vision-count-small", "vision", "How many blue circles are shown?" + json_number, 3, "shapes.png"),
        Case("vision-count-mixed", "vision", "How many orange squares are shown?" + json_number, 4, "shapes.png"),
        Case("vision-absent", "vision", "Is a green triangle present?" + json_boolean, False, "objects.png"),
        Case("vision-present", "vision", "Is a purple triangle present?" + json_boolean, True, "objects.png"),
        Case("vision-ocr-label", "vision", "Read the large code on the label." + json_string, "K7M-42", "label.png"),
        Case("vision-ocr-badge", "vision", "Read the large code on the badge." + json_string, "RP6-31", "badge.png"),
        context_case("context-short-start", 8, 0, "X7-25"),
        context_case("context-short-end", 8, 7, "U2-71"),
        context_case("context-long-start", 90, 4, "Q4-12"),
        context_case("context-long-middle", 90, 44, "Y3-26"),
        context_case("context-long-end", 90, 84, "W8-54"),
        *challenge_agent_cases(json_number, json_string),
        *(challenge_context_case(size, scenario) for size in ("short", "long")
          for scenario in ("revision", "join", "filter", "absence")),
    ]
    return cases


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON key")
    return result


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def parse_json(text):
    return json.loads(text.strip(), object_pairs_hook=unique_object,
                      parse_float=finite_float, parse_constant=finite_float)


def equal_value(actual, expected, tolerance=0.0, *, strict_integers=False):
    if type(expected) in (int, float):
        if type(actual) not in (int, float):
            return False
        if strict_integers and type(expected) is int and type(actual) is not int:
            return False
        if tolerance == 0:
            return actual == expected
        return expected - tolerance <= actual <= expected + tolerance
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            equal_value(actual[key], value, strict_integers=strict_integers)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            equal_value(item, value, strict_integers=strict_integers)
            for item, value in zip(actual, expected)
        )
    return actual == expected


def score_answer(case, content):
    try:
        actual = parse_json(content)
    except (ValueError, RecursionError):
        return "invalid_json"
    if case.response_schema is not None:
        if not isinstance(actual, dict) or actual.keys() != {"answer"}:
            return "wrong_answer"
        actual = actual["answer"]
    if not equal_value(actual, case.answer, case.tolerance, strict_integers=case.track == "structured"):
        return "wrong_answer"
    return "pass"


def score_tools(case, trace, state):
    expected_calls = [{"name": name, "arguments": args} for name, args in case.calls]
    actual_calls = [{"name": call["name"], "arguments": call["arguments"]} for call in trace]
    if not equal_value(actual_calls, expected_calls):
        return "wrong_tool_trace"
    if case.state is not None and state["indicator"] != case.state:
        return "wrong_final_state"
    return "pass"


def score(case, content, trace, state):
    answer_status = score_answer(case, content)
    return score_tools(case, trace, state) if answer_status == "pass" else answer_status
