import base64
import math
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import permutations
from pathlib import Path
from unittest.mock import Mock, patch

from cases import ROOT, Case, dataset, dispatch, score, score_tools
from light_eval import COLD, DECODE_PROMPT, WARMUP, build_parser, print_summary, rounds, run_case, run_locked, run_slot, summarize
from runtime import BenchError, Ollama, Telemetry, mac_memory, mac_total_memory_kib, range_stats

CASES = {case.prompt: case for case in dataset()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, body, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path == "/api/tags":
            self.respond({"models": self.server.models})
        elif self.path == "/api/ps":
            self.respond({"models": self.server.loaded})
        elif self.path == "/api/version":
            self.respond({"version": "mock-1"})
        elif self.path == "/api/slow":
            self.send_response(200)
            self.end_headers()
            time.sleep(.3)
        else:
            self.respond({"error": "missing"}, 404)

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = request["model"]
        if self.path == "/api/show":
            capabilities = ["completion", "thinking"]
            if model == "alpha:latest":
                capabilities += ["tools", "vision"]
            self.respond({"capabilities": capabilities, "thinking": {"values": [False, True]},
                          "template": "fixture"})
            return
        if self.path == "/api/generate":
            if request.get("keep_alive") != 0:
                self.respond({"error": "expected unload"}, 400)
                return
            self.server.loaded = []
            self.respond({"done": True})
            return
        if self.path != "/api/chat" or request.get("stream") is not True:
            self.respond({"error": "expected stream"}, 400)
            return
        self.server.loaded = [item for item in self.server.models if item["name"] == model]
        self.server.requests.append(request)
        prompt = request["messages"][0]["content"]
        case = CASES.get(prompt)
        if case and case.response_schema is not None and "tools" not in request:
            assert request.get("format") == case.response_schema
        else:
            assert "format" not in request
        if case and case.image:
            images = request["messages"][0]["images"]
            assert base64.b64decode(images[0]) == (ROOT / "fixtures" / case.image).read_bytes()
        if case and case.track == "agent":
            if "tools" in request:
                assert request["tools"] == case.tools
            else:
                assert request.get("format") == case.response_schema
            results = [msg for msg in request["messages"] if msg["role"] == "tool"]
            if case.calls[:len(results)]:
                assert [msg["tool_name"] for msg in results] == [name for name, _ in case.calls[:len(results)]]
            if len(results) < len(case.calls):
                name, args = case.calls[len(results)]
                message = {"tool_calls": [{"function": {"name": name, "arguments": args}}]}
            else:
                message = {"content": json.dumps({"answer": case.answer})}
        else:
            value = {"answer": case.answer} if case and case.response_schema is not None else (
                case.answer if case else "ready")
            message = {"content": json.dumps(value)}
        if "content" in message:
            content = message["content"]
            events = [{"message": {"content": content[:1]}}, {"message": {"content": content[1:]}}]
        else:
            events = [{"message": message}]
        events.append({"done": True, "done_reason": "stop", "eval_count": 8, "eval_duration": 200000000,
                       "prompt_eval_count": 24, "prompt_eval_cached_count": 2,
                       "prompt_eval_duration": 100000000, "load_duration": 50000000})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for event in events:
                self.wfile.write((json.dumps(event) + "\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class BenchmarkTest(unittest.TestCase):
    def scripted_case(self, case, replies, *options):
        args = build_parser().parse_args(["run", "--models", "fixture", *options])
        api = Mock()
        requests = []
        responses = iter(replies)

        def chat(body, remaining, turn):
            requests.append((deepcopy(body), remaining))
            reply = next(responses)
            if isinstance(reply, BenchError):
                raise reply
            turn.update({"metrics": {"done_reason": "stop", "eval_count": 1}, **reply})

        api.chat.side_effect = chat
        metadata = {"capabilities": ["completion", "tools", "vision"], "thinking_requested": False}
        images = {case.image: "fixture-image"} if case.image else {}
        result = run_case(api, args, "fixture", metadata, case, images)
        return result, requests

    def test_answer_format_by_track(self):
        expected_types = {
            "math-multiply": "number", "math-fraction": "number",
            "vision-count-small": "number", "vision-present": "boolean",
            "vision-ocr-label": "string", "context-short-start": "string",
        }
        selected = [case for case in dataset() if case.id in expected_types or case.track == "structured"]
        selected += [COLD, WARMUP, Case("decode", "performance", DECODE_PROMPT, None)]
        for case in selected:
            with self.subTest(case=case.id):
                value = {"answer": case.answer} if case.id in expected_types else case.answer
                result, requests = self.scripted_case(case, [{"content": json.dumps(value)}])
                body = requests[0][0]
                if case.track == "context":
                    self.assertFalse(body["truncate"])
                if case.id in expected_types:
                    expected = {"type": "object", "properties": {"answer": {"type": expected_types[case.id]}},
                                "required": ["answer"], "additionalProperties": False}
                    self.assertEqual(body["format"], expected)
                    self.assertEqual(result["response_schema"], expected)
                else:
                    self.assertNotIn("format", body)
                    self.assertIsNone(result["response_schema"])
                self.assertEqual(result["status"], "complete" if case.track == "performance" else "pass")

    def test_native_mac_backend_and_explicit_cpu_mode(self):
        case = next(case for case in dataset() if case.id == "math-multiply")
        reply = {"content": '{"answer":5017}'}
        with patch("light_eval.platform.system", return_value="Darwin"):
            _, native = self.scripted_case(case, [reply])
            _, cpu = self.scripted_case(case, [reply], "--cpu-only")
        with patch("light_eval.platform.system", return_value="Linux"):
            _, pi = self.scripted_case(case, [reply])
        self.assertNotIn("num_gpu", native[0][0]["options"])
        self.assertEqual(cpu[0][0]["options"]["num_gpu"], 0)
        self.assertEqual(pi[0][0]["options"]["num_gpu"], 0)
        self.assertEqual(native[0][0]["messages"], cpu[0][0]["messages"])
        self.assertEqual(native[0][0]["format"], cpu[0][0]["format"])

    def test_agent_tools_then_constrained_final_answer(self):
        case = next(case for case in dataset() if case.id == "agent-second-order")
        replies = [{"tool_calls": [{"function": {"name": name, "arguments": values}}]}
                   for name, values in case.calls]
        replies += [{"content": "The order has been delivered."}, {"content": '{"answer":"delivered"}'}]
        result, requests = self.scripted_case(case, replies)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["agent_evaluation"], {"tool_use_correct": True, "final_answer_correct": True})
        self.assertEqual(len(requests), 4)
        for body, _ in requests[:-1]:
            self.assertIn("tools", body)
            self.assertNotIn("format", body)
        final = requests[-1][0]
        self.assertNotIn("tools", final)
        self.assertEqual(final["format"], {"type": "object", "properties": {"answer": {"type": "string"}},
                                           "required": ["answer"], "additionalProperties": False})
        self.assertEqual(final["messages"][-2]["content"], "The order has been delivered.")
        self.assertEqual([msg["tool_name"] for msg in final["messages"] if msg["role"] == "tool"],
                         ["find_order", "read_order"])
        self.assertEqual(result["generated_tokens"], 4)
        self.assertEqual([turn["format"] for turn in result["turns"]],
                         [None, None, None, final["format"]])

    def test_agent_direct_answer_and_missing_required_tool(self):
        for case_id, content, expected in (("agent-direct", '{"answer":"ready"}', "pass"),
                                          ("agent-calculate", '{"answer":5017}', "wrong_tool_trace")):
            with self.subTest(case=case_id):
                case = next(case for case in dataset() if case.id == case_id)
                result, requests = self.scripted_case(case, [{"content": content}, {"content": content}])
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[-1][0]["format"], case.response_schema)
                self.assertEqual(result["agent_evaluation"]["tool_use_correct"], expected == "pass")
                self.assertTrue(result["agent_evaluation"]["final_answer_correct"])

    def test_agent_sentence_answers_preserve_tool_use_credit(self):
        sentences = {
            "agent-order": "The shipping status for Mira's order is in transit.",
            "agent-second-order": "The shipping status for the order is delivered.",
            "agent-indicator": "The simulated indicator is now green.",
            "agent-off": "The simulated indicator is now off.",
        }
        for case in dataset():
            if case.id not in sentences:
                continue
            with self.subTest(case=case.id):
                replies = [{"tool_calls": [{"function": {"name": name, "arguments": values}}]}
                           for name, values in case.calls]
                replies += [{"content": sentences[case.id]},
                            {"content": json.dumps({"answer": sentences[case.id]})}]
                result, _ = self.scripted_case(case, replies)
                self.assertEqual(result["status"], "wrong_answer")
                self.assertEqual(result["agent_evaluation"],
                                 {"tool_use_correct": True, "final_answer_correct": False})

    def test_agent_tool_errors_do_not_receive_partial_trace_credit(self):
        case = next(case for case in dataset() if case.id == "agent-calculate")
        name, values = case.calls[0]
        call = {"tool_calls": [{"function": {"name": name, "arguments": values}}]}
        unknown = {"tool_calls": [{"function": {"name": "unknown", "arguments": {}}}]}
        for replies, options in (([call, unknown], []), ([call, call], ["--tool-calls", "1"])):
            result, _ = self.scripted_case(case, replies, *options)
            self.assertIn(result["status"], ("invalid_tool_call", "tool_limit"))
            self.assertEqual(len(result["tool_trace"]), 1)
            self.assertEqual(result["agent_evaluation"],
                             {"tool_use_correct": False, "final_answer_correct": False})

    def test_agent_initial_error_does_not_count_as_correct_abstention(self):
        case = next(case for case in dataset() if case.id == "agent-direct")
        result, _ = self.scripted_case(case, [BenchError("No response", "api_error")])
        self.assertEqual(result["agent_evaluation"],
                         {"tool_use_correct": False, "final_answer_correct": False})

    def test_tool_use_requires_correct_final_state(self):
        case = next(case for case in dataset() if case.id == "agent-indicator")
        trace = [{"name": name, "arguments": values} for name, values in case.calls]
        self.assertEqual(score_tools(case, trace, {"indicator": "red"}), "wrong_final_state")
        self.assertEqual(score_tools(case, trace, {"indicator": "green"}), "pass")

    def test_agent_summary_separates_tool_use_answer_and_end_to_end(self):
        rows = []
        for index, (status, tools, answer) in enumerate((
            ("wrong_answer", True, False), ("wrong_tool_trace", False, True),
            ("pass", True, True), ("timeout", False, False),
        )):
            rows.append({"model": "fixture", "phase": "warm", "track": "agent", "case": str(index),
                         "round": 1, "status": status,
                         "agent_evaluation": {"tool_use_correct": tools, "final_answer_correct": answer}})
        rows.append({"model": "fixture", "phase": "warm", "track": "agent", "case": "unsupported",
                     "round": 1, "status": "unsupported"})
        summary = summarize(rows, [])
        group = summary["groups"][0]
        self.assertEqual(group["pass_rate"], .25)
        self.assertEqual(group["agent_metrics"], {
            "n": 4, "tool_use_passed": 2, "tool_use_pass_rate": .5,
            "final_answer_passed": 2, "final_answer_pass_rate": .5,
        })
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            print_summary(summary)
        self.assertIn("1/4 end-to-end passed", output.getvalue())
        self.assertIn("2/4 tool use", output.getvalue())
        self.assertIn("2/4 exact answers", output.getvalue())

    def test_agent_legacy_report_keeps_original_scores(self):
        row = {"model": "fixture", "phase": "warm", "track": "agent", "case": "legacy",
               "round": 1, "status": "wrong_answer"}
        summary = summarize([row], [])
        self.assertIsNone(summary["groups"][0]["agent_metrics"])
        self.assertEqual(summary["groups"][0]["outcomes"], {"wrong_answer": 1})
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            print_summary(summary)
        self.assertIn("tool-use checks unavailable", output.getvalue())
        new_row = {**row, "case": "new", "agent_evaluation":
                   {"tool_use_correct": True, "final_answer_correct": False}}
        self.assertIsNone(summarize([row, new_row], [])["groups"][0]["agent_metrics"])

    def test_requests_do_not_depend_on_expected_answers_or_scoring_rules(self):
        for case in dataset():
            with self.subTest(case=case.id):
                value = {"answer": case.answer} if case.response_schema is not None else case.answer
                content = {"content": json.dumps(value)}
                replies = [{"tool_calls": [{"function": {"name": name, "arguments": args}}]}
                           for name, args in case.calls]
                replies.append(content)
                if case.track == "agent":
                    replies.append(content)
                _, original_requests = self.scripted_case(case, replies)
                alternative = {int: -987654, float: -98765.4321, bool: not case.answer,
                               str: "unseen-audit-value", dict: {"audit": "value"}, list: ["audit"]}
                changed = replace(case, answer=alternative[type(case.answer)], calls=(),
                                  state=None, tolerance=9999)
                _, changed_requests = self.scripted_case(changed, replies)
                self.assertEqual([body for body, _ in original_requests],
                                 [body for body, _ in changed_requests])

    def test_scoring_generalizes_to_unseen_values_without_substring_matching(self):
        rng = random.Random(20261116)
        for _ in range(50):
            value = rng.randrange(-1000000, 1000000)
            case = Case("unseen-arithmetic", "arithmetic", "", value)
            self.assertEqual(score(case, json.dumps({"answer": value}), [], {}), "pass")
            self.assertEqual(score(case, json.dumps({"answer": value + 1}), [], {}), "wrong_answer")
            self.assertEqual(score(case, json.dumps({"answer": str(value)}), [], {}), "wrong_answer")
        case = Case("unseen-context", "context", "", "awaiting collection")
        for content in ('{"answer":"awaiting collection"}',
                        ' { "answer" : "awaiting collection" }\n',
                        '{"answ\\u0065r":"awaiting collection"}'):
            self.assertEqual(score(case, content, [], {}), "pass")
        for value in ("The order is awaiting collection.", "not awaiting collection", "awaiting collection soon"):
            self.assertEqual(score(case, json.dumps({"answer": value}), [], {}), "wrong_answer")
        self.assertEqual(score(case, '{"answer":"awaiting collection","extra":1}', [], {}), "wrong_answer")

    def test_agent_pipeline_handles_unseen_customer_order_and_status(self):
        case = Case("unseen-agent", "agent",
                    'Look up customer avery and report the exact order status in a JSON answer object.',
                    "awaiting collection", calls=(("find_order", {"customer": "avery"}),
                                                   ("read_order", {"order_id": "ZZ-902"})))
        replies = [{"tool_calls": [{"function": {"name": name, "arguments": values}}]}
                   for name, values in case.calls]
        replies += [{"content": "The order is awaiting collection."},
                    {"content": '{"answer":"awaiting collection"}'}]
        with patch.dict("cases.CUSTOMERS", {"avery": "ZZ-902"}), patch.dict(
                "cases.ORDERS", {"ZZ-902": "awaiting collection"}):
            result, requests = self.scripted_case(case, replies)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["agent_evaluation"], {"tool_use_correct": True, "final_answer_correct": True})
        self.assertNotIn("awaiting collection", json.dumps(requests[0][0]))
        self.assertEqual(result["tool_trace"][-1]["result"], {"status": "awaiting collection"})
        final_tool_results = [message for message in requests[-1][0]["messages"] if message["role"] == "tool"]
        self.assertEqual(json.loads(final_tool_results[-1]["content"]), {"status": "awaiting collection"})

    def test_expanded_suite_preserves_controls_and_fits_tool_budgets(self):
        cases = dataset()
        self.assertEqual(cases, dataset())
        self.assertEqual(len(cases), len({case.id for case in cases}))
        self.assertEqual(Counter(case.track for case in cases),
                         {"arithmetic": 12, "structured": 10, "agent": 13, "vision": 10, "context": 13})
        self.assertEqual(Counter(case.track for case in cases if case.difficulty == "challenge"),
                         {"arithmetic": 4, "structured": 4, "agent": 7, "vision": 4, "context": 8})
        for case in cases:
            if case.image:
                self.assertTrue((ROOT / "fixtures" / case.image).is_file(), case.image)
        args = build_parser().parse_args(["run", "--models", "fixture"])
        for case in cases:
            if case.track != "agent":
                continue
            with self.subTest(case=case.id):
                offered = {tool["function"]["name"] for tool in case.tools}
                self.assertTrue(all(name in offered for name, _ in case.calls))
                self.assertLessEqual(len(case.calls), args.tool_calls)
                self.assertLessEqual(len(case.calls), args.tool_rounds)
                if case.difficulty == "control":
                    self.assertEqual(len(case.tools), 4)
                replies = [{"tool_calls": [{"function": {"name": name, "arguments": values}}]}
                           for name, values in case.calls]
                replies += [{"content": json.dumps({"answer": case.answer})}] * 2
                result, _ = self.scripted_case(case, replies)
                self.assertEqual(result["status"], "pass")
                self.assertTrue(result["agent_evaluation"]["tool_use_correct"])
                if case.state is not None:
                    self.assertEqual(result["final_state"]["indicator"], case.state)

    def test_advanced_agent_oracles_and_state_reset(self):
        cases = {case.id: case for case in dataset()}
        arithmetic = cases["agent-chain-arithmetic"]
        state = {"indicator": arithmetic.initial_state}
        values = [dispatch(name, args, state) for name, args in arithmetic.calls]
        self.assertEqual(arithmetic.calls[1][1]["a"], values[0])
        self.assertEqual(arithmetic.calls[2][1]["a"], values[1])
        self.assertEqual(arithmetic.answer, values[-1])
        for case_id in ("agent-status-branch", "agent-cross-order"):
            case = cases[case_id]
            state = {"indicator": case.initial_state}
            results = [dispatch(name, args, state) for name, args in case.calls]
            statuses = [result["status"] for result in results if isinstance(result, dict) and "status" in result]
            wanted = ("off" if statuses[0] == "delivered" else "green") if case_id == "agent-status-branch" else (
                "green" if all(status == "delivered" for status in statuses) else "red")
            self.assertEqual(state["indicator"], wanted)
            self.assertEqual(case.answer, wanted)
        missing = cases["agent-missing-customer"]
        self.assertIsNone(dispatch(*missing.calls[0], {"indicator": "red"})["order_id"])
        self.assertEqual(len(missing.calls), 1)
        noop = cases["agent-no-op-state"]
        self.assertEqual(noop.initial_state, "green")
        self.assertEqual(noop.calls, (("read_indicator", {}),))
        state = {"indicator": "red"}
        self.assertEqual(dispatch("read_indicator", {}, state), {"indicator": "red"})
        self.assertEqual(state, {"indicator": "red"})

    def test_context_challenge_answers_from_independent_log_queries(self):
        for case in dataset():
            if case.track != "context" or case.difficulty != "challenge":
                continue
            with self.subTest(case=case.id):
                question, log = case.prompt.split("\n\nDispatch log (data records only):\n")
                lines = log.split("\n\nReturn only", 1)[0].splitlines()
                expected_count = 16 if "-short-" in case.id else 96
                self.assertEqual(len(lines), expected_count)
                records = []
                for line in lines:
                    parts = line.split()
                    fields = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
                    records.append((parts[0], fields))
                    if "shelf" in fields:
                        self.assertRegex(fields["shelf"], r"^S\d{3}$")
                if case.id.endswith("revision"):
                    target = re.search(r"exact relay ID (TX-\d+)", question)[1]
                    matching = [fields for kind, fields in records if kind == "ASSIGN" and fields["relay"] == target]
                    approved = sorted((row for row in matching if row["state"] == "approved"),
                                      key=lambda row: int(row["revision"]))
                    answer = approved[-1]["shelf"]
                    self.assertNotEqual(approved[-2]["shelf"], answer)
                    self.assertTrue(any(int(row["revision"]) > int(approved[-1]["revision"]) for row in matching))
                elif case.id.endswith("join"):
                    alias = re.search(r"alias (pump-\d+)", question)[1]
                    aliases = {row["name"]: row["relay"] for kind, row in records if kind == "ALIAS"}
                    relays = {row["id"]: row["route"] for kind, row in records if kind == "RELAY"}
                    routes = {row["id"]: row["shelf"] for kind, row in records if kind == "ROUTE"}
                    answer = routes[relays[aliases[alias]]]
                    self.assertIn(alias + "0", aliases)
                elif case.id.endswith("filter"):
                    eligible = [row for kind, row in records if kind == "NODE" and row["zone"] == "Z4"
                                and row["state"] == "active" and int(row["temp"]) >= 70]
                    answer = len(eligible)
                    self.assertTrue(any(row["temp"] == "70" for row in eligible))
                    self.assertTrue(any(row["zone"] == "Z40" for _, row in records))
                else:
                    target = re.search(r"exact relay ID (TX-\d+)", question)[1]
                    assignments = [row for kind, row in records if kind == "ASSIGN" and row["relay"] == target]
                    self.assertEqual(assignments, [])
                    self.assertTrue(any(kind == "COMMENT" and row["relay"] == target for kind, row in records))
                    answer = "not found"
                self.assertEqual(case.answer, answer)
                self.assertEqual(score(case, json.dumps({"answer": answer}), [], {}), "pass")

    def test_unadvertised_tool_is_rejected_and_call_ids_are_preserved(self):
        direct = next(case for case in dataset() if case.id == "agent-direct")
        result, _ = self.scripted_case(direct, [{"tool_calls": [
            {"function": {"name": "read_indicator", "arguments": {}}}]}])
        self.assertEqual(result["status"], "invalid_tool_call")
        self.assertFalse(result["agent_evaluation"]["tool_use_correct"])
        case = next(case for case in dataset() if case.id == "agent-calculate")
        name, args = case.calls[0]
        result, requests = self.scripted_case(case, [
            {"tool_calls": [{"id": "call-test-1", "function": {"name": name, "arguments": args}}]},
            {"content": '{"answer":5017}'}, {"content": '{"answer":5017}'}])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(requests[1][0]["messages"][-1]["tool_call_id"], "call-test-1")

    def test_commutative_calculator_operands_are_equivalent(self):
        case = next(case for case in dataset() if case.id == "agent-chain-arithmetic")
        trace = [{"name": name, "arguments": dict(args)} for name, args in case.calls]
        trace[1]["arguments"]["a"], trace[1]["arguments"]["b"] = 86, 703
        self.assertEqual(score_tools(case, trace, {"indicator": "red"}), "pass")
        trace[2]["arguments"]["a"], trace[2]["arguments"]["b"] = 3, 789
        self.assertEqual(score_tools(case, trace, {"indicator": "red"}), "wrong_tool_trace")

    def test_structured_answers_accept_one_code_fence(self):
        case = next(case for case in dataset() if case.id == "json-null")
        for content, expected in (('```json\n{"ticket": "P-42", "owner": null}\n```', "pass"),
                                  ('```\n{"ticket": "P-42", "owner": null}\n```', "pass"),
                                  ('Here it is: {"ticket": "P-42", "owner": null}', "invalid_json")):
            with self.subTest(content=content):
                self.assertEqual(score(case, content, [], {"indicator": "red"}), expected)

    def test_summary_reports_controls_and_challenges_separately(self):
        rows = [{"model": "fixture", "phase": "warm", "track": "context", "round": 1,
                 "case": "context-short-" + level, "difficulty": level, "status": status}
                for level, status in (("control", "pass"), ("challenge", "wrong_answer"))]
        summary = summarize(rows, [])
        levels = summary["groups"][0]["by_difficulty"]
        self.assertEqual(levels["control"]["pass_rate"], 1.0)
        self.assertEqual(levels["challenge"]["pass_rate"], 0.0)
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            print_summary(summary)
        self.assertIn("control 1/1, challenge 0/1", output.getvalue())

    def test_constrained_answers_still_validate_values_and_failures(self):
        case = next(case for case in dataset() if case.id == "math-multiply")
        for reply, expected in (
            ({"content": '{"answer":4917}'}, "wrong_answer"),
            ({"content": '{"answer":"5017"}'}, "wrong_answer"),
            ({"content": "```json\n{\"answer\":5017}\n```"}, "pass"),
            ({"content": "```json\n{\"answer\":5017}\n```\nDone."}, "invalid_json"),
            ({"content": '{"answer":501', "metrics": {"done_reason": "length", "eval_count": 128}},
             "generation_limit"),
            (BenchError("Schema rejected", "api_error"), "api_error"),
        ):
            with self.subTest(status=expected, reply=repr(reply)):
                result, requests = self.scripted_case(case, [reply])
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0][0]["messages"][0]["content"], case.prompt)
                self.assertEqual(requests[0][0]["format"],
                                 {"type": "object", "properties": {"answer": {"type": "number"}},
                                  "required": ["answer"], "additionalProperties": False})

    def test_agent_final_answer_failures(self):
        case = next(case for case in dataset() if case.id == "agent-direct")
        for reply, expected in (
            ({"content": '{"answer":"rea', "metrics": {"done_reason": "length", "eval_count": 128}},
             "generation_limit"),
            ({"content": '{"answer":"wrong"}'}, "wrong_answer"),
            ({"content": "ready"}, "invalid_json"),
            ({"tool_calls": [{"function": {"name": "find_order", "arguments": {"customer": "mira"}}}]},
             "invalid_tool_call"),
            (BenchError("Schema rejected", "api_error"), "api_error"),
        ):
            with self.subTest(status=expected):
                result, requests = self.scripted_case(case, [{"content": "ready"}, reply])
                self.assertEqual(result["status"], expected)
                self.assertEqual(len(requests), 2)
                self.assertFalse(result["agent_evaluation"]["final_answer_correct"])

    def test_agent_final_answer_shares_case_deadline(self):
        case = next(case for case in dataset() if case.id == "agent-direct")
        for budget, options in ((600, ()), (90, ("--timeout", "90"))):
            with self.subTest(budget=budget), patch(
                    "light_eval.time.monotonic", side_effect=[0, 1, budget + 1, budget + 2]):
                result, requests = self.scripted_case(case, [{"content": "ready"}], *options)
            self.assertEqual(result["status"], "timeout")
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0][1], budget - 1)
            self.assertEqual(result["agent_evaluation"],
                             {"tool_use_correct": True, "final_answer_correct": False})

    def test_agent_tool_budget_still_applies(self):
        case = next(case for case in dataset() if case.id == "agent-second-order")
        replies = [{"tool_calls": [{"function": {"name": name, "arguments": values}}]}
                   for name, values in case.calls]
        for option in ("--tool-rounds", "--tool-calls"):
            with self.subTest(option=option):
                result, requests = self.scripted_case(case, replies, option, "1")
                self.assertEqual(result["status"], "tool_limit")
                self.assertTrue(all("format" not in body for body, _ in requests))
                self.assertFalse(result["agent_evaluation"]["tool_use_correct"])

    def test_balanced_seeded_schedule(self):
        for count in range(1, 6):
            models = list("abcde"[:count])
            for cycles in range(1, 5):
                with self.subTest(models=count, cycles=cycles):
                    scheduled = list(rounds(models, cycles, 31))
                    self.assertEqual(len(scheduled), count * cycles)
                    self.assertEqual(scheduled, list(rounds(models[::-1], cycles, 31)))
                    for start in range(0, len(scheduled), count):
                        for column in zip(*scheduled[start:start + count]):
                            self.assertEqual(Counter(column), Counter(models))

    def test_three_models_cover_all_orders_in_each_cycle_pair(self):
        models = ["a", "b", "c"]
        expected = Counter(permutations(models))
        for seed in range(100):
            with self.subTest(seed=seed):
                scheduled = list(rounds(models, 4, seed))
                for start in (0, 6):
                    self.assertEqual(Counter(map(tuple, scheduled[start:start + 6])), expected)

    def test_paired_cycles_reverse_order_after_each_leading_model(self):
        for count in (3, 4, 5):
            models = list("abcde"[:count])
            scheduled = list(rounds(models, 2, 31))
            expected = {tuple(row[:1] + row[:0:-1]) for row in scheduled[:count]}
            self.assertEqual(set(map(tuple, scheduled[count:])), expected)

    def test_typed_scoring(self):
        arithmetic = Case("test", "arithmetic", "", 1)
        self.assertEqual(score(arithmetic, '{"answer":1.0}', [], {}), "pass")
        self.assertEqual(score(arithmetic, '{"answer":true}', [], {}), "wrong_answer")
        self.assertEqual(score(arithmetic, '{"answer":NaN}', [], {}), "invalid_json")
        self.assertEqual(score(arithmetic, '{"answer":1e9999}', [], {}), "invalid_json")
        structured = Case("test", "structured", "", {"count": 1})
        self.assertEqual(score(structured, '{"count":1.0}', [], {}), "wrong_answer")
        self.assertEqual(score(structured, '{"count":1,"count":2}', [], {}), "invalid_json")
        with self.assertRaises(ValueError):
            dispatch("calculate", {"operation": "add", "a": True, "b": 2}, {})

    def test_answer_envelope_requires_exact_keys_and_value_type(self):
        case = Case("test", "arithmetic", "", 1)
        for content in ('1', '"1"', '[1]', '{}', '{"result":1}', '{"answer":1,"extra":0}',
                        '{"answer":"1"}', '{"answer":true}', '{"answer":{"value":1}}'):
            with self.subTest(content=content):
                self.assertEqual(score(case, content, [], {}), "wrong_answer")
        self.assertEqual(score(case, '{"answer":1,"answer":1}', [], {}), "invalid_json")
        boolean = Case("test", "vision", "", False)
        self.assertEqual(score(boolean, '{"answer":false}', [], {}), "pass")
        self.assertEqual(score(boolean, '{"answer":0}', [], {}), "wrong_answer")
        fraction = next(case for case in dataset() if case.id == "math-fraction")
        self.assertEqual(score(fraction, '{"answer":0.2857}', [], {}), "pass")
        self.assertEqual(score(fraction, '{"answer":0.2855}', [], {}), "wrong_answer")

    def test_non_structured_prompts_request_answer_objects(self):
        for case in dataset():
            if case.track == "structured":
                self.assertIsNone(case.response_schema)
                continue
            with self.subTest(case=case.id):
                self.assertIn("JSON object", case.prompt)
                self.assertIn('"answer"', case.prompt)
                self.assertNotIn("Return only a JSON number", case.prompt)
                self.assertNotIn("Return only a JSON string", case.prompt)

    def test_telemetry(self):
        stats = range_stats([
            {"cpu_c": 51.2, "MemAvailable": 5000, "MemTotal": 6000, "pswpout": 4,
             "throttled_bits": 1 << 16},
            {"cpu_c": 60.3, "MemAvailable": 4000, "MemTotal": 6000, "pswpout": 7,
             "throttled_bits": (5 << 16) | 4},
        ])
        self.assertEqual(stats["peak_system_used_estimate_kib"], 2000)
        self.assertEqual(stats["swap_out_pages"], 3)
        self.assertEqual(stats["active_throttling_bits_or"], 4)
        self.assertEqual(stats["new_throttling_history_bits"], 4)

    def test_macos_memory_and_swap_units(self):
        vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
              "Pages free: 4.\nPages wired down: 10.\nPages occupied by compressor: 3.\n"
              "Swapins: 5.\nSwapouts: 7.\n")
        outputs = {("vm_stat",): vm, ("sysctl", "-n", "hw.memsize"): "8589934592\n",
                   ("sysctl", "-n", "vm.swapusage"): "total = 4096.00M used = 1536.50M free = 2559.50M",
                   ("sysctl", "-n", "kern.memorystatus_vm_pressure_level"): "1"}
        mac_total_memory_kib.cache_clear()
        self.addCleanup(mac_total_memory_kib.cache_clear)
        with patch("runtime.command_output", side_effect=lambda args: outputs[tuple(args)]):
            row = mac_memory()
        self.assertEqual(row["MemTotal"], 8 * 1024**2)
        self.assertEqual(row["MemFree"], 64)
        self.assertEqual(row["memory_wired_kib"], 160)
        self.assertEqual(row["memory_compressed_kib"], 48)
        self.assertEqual(row["memory_pressure_level"], 1)
        self.assertEqual(row["SwapUsed"], 1536.5 * 1024)
        self.assertEqual((row["pswpin"], row["pswpout"]), (5, 7))
        self.assertNotIn("MemAvailable", row)
        later = {**row, "pswpin": 7, "pswpout": 10, "memory_compressed_kib": 80}
        stats = range_stats([row, later])
        self.assertEqual(stats["swap_in_mib"], 2 * 16384 / 2**20)
        self.assertEqual(stats["swap_out_mib"], 3 * 16384 / 2**20)
        self.assertEqual(stats["peak_compressed_memory_kib"], 80)
        self.assertIsNone(stats["peak_system_used_estimate_kib"])

    def test_memory_telemetry_is_passive_even_when_pressure_and_swap_increase(self):
        warning = {"t": 0, "memory_pressure_level": 2, "pswpout": 100,
                   "memory_page_bytes": 16384}
        later = {**warning, "t": 1, "pswpout": 110}
        with patch("runtime.sample", side_effect=[warning, later]):
            with Telemetry(io.StringIO(), 1000) as monitor:
                stats = monitor.stats(warning)
        self.assertEqual(stats["swap_out_mib"], 10 * 16384 / 2**20)

    def test_warm_cases_reload_every_n_cases(self):
        for every in (1, 10):
            with self.subTest(every=every):
                self.check_reload_every(every)

    def check_reload_every(self, every):
        candidates = dataset()
        args = build_parser().parse_args(["run", "--models", "fixture", "--phases", "warm", "--no-thermal-gate",
                                             "--reload-every", str(every)])
        api, monitor = Mock(), Mock()
        metadata = {"digest": "fixture-digest", "capabilities": ["completion", "tools", "vision"]}
        resident = {"digest": "fixture-digest"}
        live, prompts, events = [], [], []
        api.loaded.side_effect = lambda: live[:]
        api.json.return_value = {"models": [{"name": "fixture:latest", "digest": "fixture-digest"}]}

        def unload(model):
            if live:
                events.append("unload")
            live.clear()
            prompts.clear()

        def generate(api, args, model, metadata, case, images, **kwargs):
            if case is WARMUP:
                self.assertEqual(live, [])
                self.assertEqual(prompts, [])
                live.append(resident)
                prompts.append("warmup")
                events.append("warmup")
            else:
                self.assertEqual(live, [resident])
                self.assertEqual(prompts[0], "warmup")
                prompts.append(case.id)
                events.append(case.id)
            return {"status": "complete" if case is WARMUP else "pass"}

        api.unload.side_effect = unload
        monitor.take.return_value = monitor.gate.return_value = {"t": 0}
        monitor.stats.return_value = {"system_oom_kills": None}
        records, blocks = io.StringIO(), io.StringIO()
        with patch("light_eval.run_case", side_effect=generate), patch("sys.stdout", new_callable=io.StringIO):
            run_slot(api, args, monitor, None,
                     {"round": 1, "position": 1, "model": "fixture:latest", "metadata": metadata},
                     candidates, {}, records, blocks)
        groups = [candidates[index:index + every] for index in range(0, len(candidates), every)]
        expected = [event for group in groups for event in ("warmup", *[case.id for case in group], "unload")]
        self.assertEqual(events, expected)
        self.assertEqual(len(records.getvalue().splitlines()), len(candidates))
        self.assertEqual(json.loads(blocks.getvalue())["warmups"], len(groups))
        self.assertEqual(live, [])

    def test_failed_warmup_fails_only_its_own_case(self):
        candidates = [case for case in dataset() if case.track == "arithmetic"][:2]
        args = build_parser().parse_args(["run", "--models", "fixture", "--phases", "warm"])
        api, monitor = Mock(), Mock()
        metadata = {"digest": "fixture-digest", "capabilities": ["completion"]}
        live, warmups = [], []
        api.loaded.side_effect = lambda: live[:]
        api.json.return_value = {"models": [{"name": "fixture:latest", "digest": "fixture-digest"}]}
        api.unload.side_effect = lambda model: live.clear()

        def generate(api, args, model, metadata, case, images, **kwargs):
            if case is WARMUP:
                warmups.append(case)
                if len(warmups) == 1:
                    return {"status": "out_of_memory", "reason": "runner crashed"}
                live.append({"digest": "fixture-digest"})
                return {"status": "complete"}
            return {"status": "pass"}

        monitor.take.return_value = monitor.gate.return_value = {"t": 0}
        monitor.stats.return_value = {"system_oom_kills": None}
        records, blocks = io.StringIO(), io.StringIO()
        with patch("light_eval.run_case", side_effect=generate), patch("sys.stdout", new_callable=io.StringIO):
            run_slot(api, args, monitor, None,
                     {"round": 1, "position": 1, "model": "fixture:latest", "metadata": metadata},
                     candidates, {}, records, blocks)
        rows = [json.loads(line) for line in records.getvalue().splitlines()]
        self.assertEqual([row["status"] for row in rows], ["out_of_memory", "pass"])
        self.assertIn("Warmup failed", rows[0]["reason"])
        self.assertEqual(json.loads(blocks.getvalue())["warmups"], 2)

    def test_out_of_memory_forces_reload_despite_stale_residency(self):
        candidates = [case for case in dataset() if case.track == "arithmetic"][:3]
        args = build_parser().parse_args(["run", "--models", "fixture", "--phases", "warm"])
        api, monitor = Mock(), Mock()
        metadata = {"digest": "fixture-digest", "capabilities": ["completion"]}
        live, events = [], []
        api.loaded.side_effect = lambda: live[:]
        api.json.return_value = {"models": [{"name": "fixture:latest", "digest": "fixture-digest"}]}
        api.unload.side_effect = lambda model: live.clear()

        def generate(api, args, model, metadata, case, images, **kwargs):
            if case is WARMUP:
                live.append({"digest": "fixture-digest"})
                events.append("warmup")
                return {"status": "complete"}
            events.append(case.id)
            # Keep /api/ps stale, as it was after a real runner crash.
            return {"status": "out_of_memory" if case is candidates[0] else "pass"}

        monitor.take.return_value = monitor.gate.return_value = {"t": 0}
        monitor.stats.return_value = {"system_oom_kills": None}
        records, blocks = io.StringIO(), io.StringIO()
        with patch("light_eval.run_case", side_effect=generate), patch("sys.stdout", new_callable=io.StringIO):
            run_slot(api, args, monitor, None,
                     {"round": 1, "position": 1, "model": "fixture:latest", "metadata": metadata},
                     candidates, {}, records, blocks)
        self.assertEqual(events, ["warmup", candidates[0].id, "warmup", candidates[1].id, candidates[2].id])
        self.assertEqual(json.loads(blocks.getvalue())["warmups"], 2)

    def test_gate_waits_for_temperature_and_active_flags(self):
        samples = [
            {"cpu_c": 64, "throttled_bits": 0},
            {"cpu_c": 55, "throttled_bits": 4},
            {"cpu_c": 55, "throttled_bits": 1 << 18},
        ]
        with patch("runtime.time.sleep"), patch("runtime.sample", side_effect=samples) as read:
            monitor = Telemetry(io.StringIO(), 1)
            self.assertEqual(monitor.gate((50, 60), 10), samples[-1])
            self.assertEqual(read.call_count, 3)
        with patch("runtime.sample", return_value={"cpu_c": None, "throttled_bits": None}):
            with self.assertRaisesRegex(BenchError, "vcgencmd unavailable"):
                monitor.gate((50, 60), 10)

    def test_cli_mock_ollama(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as tmp:
            server.models = [{"name": name, "digest": name + "-digest"} for name in ("alpha:latest", "beta:latest")]
            server.loaded = []
            server.requests = []
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = str(server.server_address[1])
                with self.assertRaises(BenchError) as caught:
                    Ollama(int(port)).json("slow", timeout=.05)
                self.assertEqual(caught.exception.status, "timeout")
                out = Path(tmp) / "eval"
                command = [sys.executable, str(ROOT / "light_eval.py"), "run", "--models", "alpha", "beta",
                           "--port", port, "--no-thermal-gate", "--cpu-only", "--sustain-seconds", ".025", "--out", str(out)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                manifest = json.loads((out / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "complete")
                self.assertEqual(manifest["suite_version"], "1.5.0")
                blocks = [json.loads(line) for line in (out / "blocks.jsonl").read_text().splitlines()]
                rows = [json.loads(line) for line in (out / "cases.jsonl").read_text().splitlines()]
                for block in blocks:
                    if block["phase"] == "warm":
                        measured = [row for row in rows if row["phase"] == "warm" and row["model"] == block["model"] and row["round"] == block["round"]
                                    and row["status"] != "unsupported"]
                        self.assertEqual(block["warmups"], math.ceil(len(measured) / 10))
                self.assertEqual(manifest["inference_options"], {"device": "cpu", "num_batch": 64})
                self.assertEqual(manifest["response_schemas"]["math-multiply"],
                                 {"type": "object", "properties": {"answer": {"type": "number"}},
                                  "required": ["answer"], "additionalProperties": False})
                self.assertIsNone(manifest["response_schemas"]["json-shipment"])
                self.assertIn("schema-constrained", manifest["agent_finalization"])
                self.assertEqual(manifest["case_metadata"]["agent-cross-order"]["difficulty"], "challenge")
                self.assertIn("read_indicator", manifest["case_metadata"]["agent-no-op-state"]["tools"])
                self.assertNotIn("read_indicator", manifest["case_metadata"]["agent-direct"]["tools"])
                self.assertIsNone(manifest["temperature_band_c"])
                self.assertEqual(len(manifest["plan"]), 2)
                self.assertEqual(server.loaded, [])
                self.assertTrue(all(req["options"]["num_gpu"] == 0 for req in server.requests))
                self.assertTrue(all(req["options"]["num_batch"] == 64 for req in server.requests))
                decode_inputs = [req["messages"][0]["content"] for req in server.requests
                                 if req["messages"][0]["content"].endswith(DECODE_PROMPT)]
                self.assertGreaterEqual(len(decode_inputs), 2)
                self.assertEqual(len(set(decode_inputs)), 1)
                self.assertTrue(all(req["think"] is False for req in server.requests))
                summary = json.loads((out / "summary.json").read_text())
                rows = {(r["model"], r["phase"], r["track"]): r for r in summary["groups"]}
                self.assertEqual(rows["alpha:latest", "warm", "agent"]["pass_rate"], 1.0)
                self.assertEqual(rows["alpha:latest", "warm", "agent"]["agent_metrics"], {
                    "n": 26, "tool_use_passed": 26, "tool_use_pass_rate": 1.0,
                    "final_answer_passed": 26, "final_answer_pass_rate": 1.0,
                })
                self.assertEqual(rows["alpha:latest", "warm", "vision"]["n"], 20)
                self.assertEqual(rows["beta:latest", "warm", "vision"]["outcomes"], {"unsupported": 20})
                self.assertEqual(rows["alpha:latest", "warm", "context-long"]["outcomes"], {"pass": 14})
                self.assertEqual(rows["alpha:latest", "cold", "performance"]["load_s"]["median"], .05)
                self.assertEqual(summary["repeatability"]["alpha:latest"]["raw_output_stable"], 58)
                self.assertEqual(len(summary["sustained"]), 4)
                self.assertTrue(all(interval["completed_requests"] > 0 for interval in summary["sustained"]))
                report = subprocess.run([sys.executable, str(ROOT / "light_eval.py"), "report", str(out)],
                                        capture_output=True, text=True, timeout=5, check=True)
                self.assertIn("12/12 passed", report.stdout)
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
