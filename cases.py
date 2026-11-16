"""Versioned fixtures and deterministic scoring for Light Eval."""
import json
import math
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "1.0"
TRACKS = ("arithmetic", "structured", "agent", "vision", "context")


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
    state["indicator"] = args["color"]
    return {"indicator": state["indicator"]}


def context_case(case_id, lines, position, code):
    records = [
        f"Relay R{index:03d} was checked by zone {index % 9 + 1} and is assigned shelf S{(index * 37) % 891:03d}."
        for index in range(lines)
    ]
    records[position] = f"Relay TX-41 is assigned shelf {code}."
    prompt = "Use the dispatch log to find the shelf for relay TX-41.\n\n" + "\n".join(records)
    return Case(case_id, "context", prompt + '\n\nReturn only a JSON string with the shelf ID.', code)


def dataset():
    json_number = " Return only a JSON number."
    json_string = " Return only a JSON string."
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
        Case("agent-direct", "agent", 'Reply with the JSON string "ready". Do not call any tools.', "ready"),
        Case("agent-order", "agent", "Find Mira's order using customer handle mira, then report only its shipping status as a JSON string.", "in transit",
             calls=(("find_order", {"customer": "mira"}), ("read_order", {"order_id": "ZX-318"}))),
        Case("agent-second-order", "agent", "Look up customer handle ren and check that customer's order. Return only its shipping status as a JSON string.", "delivered",
             calls=(("find_order", {"customer": "ren"}), ("read_order", {"order_id": "BC-915"}))),
        Case("agent-indicator", "agent", "Set the simulated indicator to green, then report its new color." + json_string, "green",
             calls=(("set_indicator", {"color": "green"}),), state="green"),
        Case("agent-off", "agent", "Switch the simulated indicator off and report its new state." + json_string, "off",
             calls=(("set_indicator", {"color": "off"}),), state="off"),
        Case("vision-count-small", "vision", "How many blue circles are shown?" + json_number, 3, "shapes.png"),
        Case("vision-count-mixed", "vision", "How many orange squares are shown?" + json_number, 4, "shapes.png"),
        Case("vision-absent", "vision", "Is a green triangle present? Return only the JSON boolean true or false.", False, "objects.png"),
        Case("vision-present", "vision", "Is a purple triangle present? Return only the JSON boolean true or false.", True, "objects.png"),
        Case("vision-ocr-label", "vision", "Read the large code on the label." + json_string, "K7M-42", "label.png"),
        Case("vision-ocr-badge", "vision", "Read the large code on the badge." + json_string, "RP6-31", "badge.png"),
        context_case("context-short-start", 8, 0, "X7-25"),
        context_case("context-short-end", 8, 7, "U2-71"),
        context_case("context-long-start", 90, 4, "Q4-12"),
        context_case("context-long-middle", 90, 44, "Y3-26"),
        context_case("context-long-end", 90, 84, "W8-54"),
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


def score(case, content, trace, state):
    try:
        actual = parse_json(content)
    except (ValueError, RecursionError):
        return "invalid_json"
    if not equal_value(actual, case.answer, case.tolerance, strict_integers=case.track == "structured"):
        return "wrong_answer"
    expected_calls = [{"name": name, "arguments": args} for name, args in case.calls]
    actual_calls = [{"name": call["name"], "arguments": call["arguments"]} for call in trace]
    if not equal_value(actual_calls, expected_calls):
        return "wrong_tool_trace"
    if case.state is not None and state["indicator"] != case.state:
        return "wrong_final_state"
    return "pass"
