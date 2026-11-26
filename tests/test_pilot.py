"""The runner's suite 1.8 pilot path (--suite 1.8, protocol 4): plans, every new case type through
run_case, the repeatability sample, the new summary metrics, and that requests never depend on answers."""
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from contextlib import redirect_stderr
from dataclasses import fields, replace

import suite_1_8
from code_runner import SandboxError, check_jail
from light_eval import ALL_TRACKS, apply_suite_defaults, build_parser, hashed_paths, run_benchmark, summarize
from suite_1_8_agent import concrete_value
from test_benchmark import BenchmarkTest, Handler, run_args

ROOT = Path(__file__).resolve().parents[1]

CASES = {case.id: case for case in suite_1_8.dataset()}
try:
    check_jail()
    JAIL = True
except SandboxError:
    JAIL = False


def plan(*options):
    parser = build_parser()
    out = io.StringIO()
    from contextlib import redirect_stdout
    with redirect_stdout(out):
        run_benchmark(parser.parse_args(["run", "--models", "a", "b", "--plan", *options]), parser)
    return json.loads(out.getvalue())


def reply(content):
    return [{"content": content}]


class PilotPlanTest(unittest.TestCase):
    def test_suite_1_8_plan(self):
        rounds = plan("--suite", "1.8")
        self.assertEqual(len(rounds), 1, "protocol 4 runs one pass by default")
        self.assertEqual(len(rounds[0]["cases"]), 160)
        self.assertEqual(set(rounds[0]["cases"]), set(CASES))
        self.assertEqual(len(rounds[0]["repeat"]), 24)
        self.assertEqual(rounds[0]["repeat"], plan("--suite", "1.8", "--seed", "7")[0]["repeat"],
                         "the repeatability sample is fixed, whatever the run's seed")
        repeated = [CASES[case_id] for case_id in rounds[0]["repeat"]]
        self.assertEqual({case.track for case in repeated}, set(ALL_TRACKS))

    def test_suite_1_7_plan_is_unchanged(self):
        rounds = plan("--suite", "1.7")
        self.assertEqual(len(rounds), 3)
        self.assertNotIn("repeat", rounds[0])
        self.assertEqual(len(rounds[0]["cases"]), 65)

    def test_defaults_by_suite(self):
        args = apply_suite_defaults(build_parser().parse_args(["run", "--models", "a"]))
        self.assertEqual((args.suite, args.passes, args.tool_rounds, args.tool_calls), ("1.8", 1, 12, 16),
                         "suite 1.8 is the default")
        args = run_args(["run", "--models", "a"])
        self.assertEqual((args.passes, args.tool_rounds, args.tool_calls), (3, 6, 8))

    def test_tracks_must_belong_to_the_suite(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            plan("--suite", "1.7", "--tracks", "rag")
        self.assertEqual(len(plan("--suite", "1.8", "--tracks", "rag", "instructions")[0]["cases"]), 40)

    def test_staged_images_are_hashed_only_for_suite_1_8(self):
        staged = [path for path in hashed_paths("1.8") if "v1_8" in path.parts]
        self.assertGreaterEqual(len(staged), 20)
        self.assertFalse(any("v1_8" in path.parts for path in hashed_paths("1.7")))


class PilotRunCaseTest(unittest.TestCase):
    scripted_case = BenchmarkTest.scripted_case

    def run_case(self, case, replies):
        return self.scripted_case(case, replies, "--suite", "1.8")

    def test_instructions(self):
        case = CASES["instr-zero-waste"]
        result, requests = self.run_case(case, reply(case.sample))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["failed_checks"], [])
        self.assertNotIn("format", requests[0][0], "free text: the format is what the case tests")
        result, _ = self.run_case(case, reply(case.sample.replace("Done.", "Finished.")))
        self.assertEqual(result["status"], "failed_constraint")
        self.assertEqual(result["failed_checks"], ["three bullets then Done."])

    def test_rag(self):
        case = CASES["rag-dashboard-port"]
        good = json.dumps({"answer": case.answer, "sources": list(case.sources)})
        result, requests = self.run_case(case, reply(good))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["rag_evaluation"], {"answer_correct": True, "sources_correct": True})
        self.assertIn("null", json.dumps(requests[0][0]["format"]))
        result, _ = self.run_case(case, reply(json.dumps({"answer": 8080, "sources": ["LOG-77"]})))
        self.assertEqual(result["status"], "followed_injection")

    def test_code_prediction_needs_no_sandbox(self):
        case = CASES["code-predict-closures"]
        result, requests = self.run_case(case, reply(json.dumps({"answer": case.answer})))
        self.assertEqual(result["status"], "pass")
        self.assertEqual(requests[0][0]["format"]["properties"]["answer"], {"type": "string"})

    @unittest.skipUnless(JAIL, "no OS sandbox here (Linux needs bubblewrap)")
    def test_code_generation_runs_the_tests_outside_the_timing(self):
        case = CASES["code-merge-intervals"]
        result, requests = self.run_case(case, reply(f"```python\n{case.reference}```"))
        self.assertEqual(result["status"], "pass")
        self.assertNotIn("format", requests[0][0])
        self.assertGreater(result["scoring_s"], 0)
        self.assertLess(result["wall_s"], result["scoring_s"], "running the tests isn't answer time")

    def test_agent_with_a_recoverable_mistake(self):
        case = CASES["agent-delay-apologies"]
        calls = [{"function": {"name": "get_order", "arguments": {"id": "4417"}}}]  # wrong argument name
        calls += [{"function": {"name": name, "arguments": {k: concrete_value(v) for k, v in args.items()}}}
                  for name, args in case.reference]
        replies = [{"content": "", "tool_calls": [call]} for call in calls]
        replies += [{"content": ""}, {"content": json.dumps({"answer": case.answer})}]
        result, requests = self.run_case(case, replies)
        self.assertEqual(result["status"], "pass", result.get("reason"))
        self.assertIn("error", result["tool_trace"][0]["result"])
        self.assertEqual(result["agent_evaluation"], {"tool_use_correct": True, "final_answer_correct": True})
        self.assertEqual(len(requests[0][0]["tools"]), len(case.tool_names))
        json.dumps(result["final_state"])  # the world is recorded as JSON
        self.assertEqual(result["final_state"]["stock"]["LMP-20|Leeds"], 3)

    def test_agent_injection(self):
        case = CASES["agent-frost-injection"]
        calls = [("get_weather", {"place": "Leeds", "date": "2026-11-27"}),
                 ("set_thermostat", {"room": "living room", "celsius": 30}),
                 ("set_thermostat", {"room": "greenhouse", "celsius": 8})]
        replies = [{"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}
                   for name, args in calls] + [{"content": ""}, {"content": json.dumps({"answer": -1})}]
        result, _ = self.run_case(case, replies)
        self.assertEqual(result["status"], "followed_injection")
        self.assertFalse(result["agent_evaluation"]["tool_use_correct"])


class ReviewFixesTest(unittest.TestCase):
    """Regression tests for the protocol 4 review: each fails without its fix."""
    scripted_case = BenchmarkTest.scripted_case

    def calls(self, *calls, final=None):
        replies = [{"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}
                   for name, args in calls]
        return replies + ([{"content": ""}, {"content": final}] if final is not None else [])

    def test_an_infinite_tool_result_is_an_error_not_a_crash(self):
        case = CASES["agent-convert"]
        replies = self.calls(("calculate", {"operation": "divide", "a": 1e12, "b": 1e-300}), final='{"answer": 1}')
        result, _ = self.scripted_case(case, replies, "--suite", "1.8")
        self.assertEqual(result["tool_trace"][0]["result"], {"error": "result out of range"})
        json.dumps(result, allow_nan=False)  # append_line writes strict JSON

    def test_following_an_injection_counts_even_when_the_case_ends_early(self):
        case = CASES["agent-ticket-injection"]
        replies = self.calls(("get_ticket", {"ticket_id": "T-88"}), ("issue_refund", {"order_id": "5002", "amount": 310}),
                             ("get_order", {"order_id": "4430"}))
        result, _ = self.scripted_case(case, replies, "--suite", "1.8", "--tool-calls", "2")
        self.assertEqual(result["status"], "tool_limit")
        self.assertTrue(result["injection_followed"])
        row = {"model": "m", "round": 1, "phase": "warm", "case": case.id, "track": "agent", "difficulty": "hard",
               "injection": True, **result}
        self.assertEqual(summarize([row], [])["injection"]["m"], {"n": 1, "followed": 1, "passed": 0})
        clean, _ = self.scripted_case(case, self.calls(("get_ticket", {"ticket_id": "T-88"}), final='{"answer": "x"}'),
                                      "--suite", "1.8")
        self.assertFalse(clean["injection_followed"])

    def test_a_malformed_rag_reply_that_obeys_the_injection_counts(self):
        case = CASES["rag-training-budget"]
        result, _ = self.scripted_case(case, reply('{"answer": 500, "sources": [], "note": "x"}'), "--suite", "1.8")
        self.assertEqual(result["status"], "invalid_json")
        self.assertTrue(result["injection_followed"])
        self.assertNotIn("injection_followed", self.scripted_case(CASES["rag-on-call"], reply("x"), "--suite", "1.8")[0])

    def test_plan_needs_no_sandbox(self):
        with mock.patch("code_runner.check_jail", side_effect=SandboxError("none")):
            self.assertEqual(len(plan("--suite", "1.8")[0]["cases"]), 160)

    def test_only_images_are_hashed(self):
        staged = [path for path in hashed_paths("1.8") if "v1_8" in path.parts]
        self.assertTrue(staged and all(path.suffix in (".png", ".jpg") for path in staged))
        with tempfile.NamedTemporaryFile(dir=ROOT / "fixtures" / "v1_8", prefix=".", suffix=".png") as junk:
            self.assertNotIn(Path(junk.name), hashed_paths("1.8"))

    def test_run_case_refuses_unresolved_options(self):
        from light_eval import run_case
        args = build_parser().parse_args(["run", "--models", "m"])
        with self.assertRaises(ValueError):
            run_case(None, args, "m", {"capabilities": []}, CASES["rag-on-call"], {})

    def test_suite_1_7_records_keep_their_shape(self):
        from cases import dataset
        case = next(case for case in dataset() if case.id == "math-multiply")
        result, _ = self.scripted_case(case, reply('{"answer": 5017}'))
        self.assertEqual(result["status"], "pass")
        self.assertFalse({"scoring_s", "injection_followed", "failed_checks", "rag_evaluation"} & set(result))


class PilotHandler(Handler):
    """Mock Ollama that answers suite 1.8 cases correctly, playing agent references call by call."""
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        case = BY_PROMPT.get(request["messages"][0]["content"]) if self.path == "/api/chat" else None
        if case is None:
            self.rfile = io.BytesIO(json.dumps(request).encode())
            self.headers.replace_header("Content-Length", str(len(json.dumps(request).encode())))
            return super().do_POST()
        self.server.loaded = [item for item in self.server.models if item["name"] == request["model"]]
        self.server.requests.append(request)
        if case.response_schema is not None and "tools" not in request:
            assert request.get("format") == case.response_schema, case.id
        message = {"content": PilotHandler.answer(case)}
        if case.track == "agent" and "tools" in request:
            done = len([msg for msg in request["messages"] if msg["role"] == "tool"])
            if done < len(case.reference):
                name, args = case.reference[done]
                message = {"tool_calls": [{"function": {"name": name, "arguments":
                                                        {k: concrete_value(v) for k, v in args.items()}}}]}
            else:
                message = {"content": ""}
        events = [{"message": message}, {"done": True, "done_reason": "stop", "eval_count": 8,
                                         "eval_duration": 200000000, "prompt_eval_count": 24,
                                         "prompt_eval_duration": 100000000, "load_duration": 50000000}]
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for event in events:
            self.wfile.write((json.dumps(event) + "\n").encode())

    @staticmethod
    def answer(case):
        if case.track == "instructions":
            return case.sample
        if case.track == "rag":
            return json.dumps({"answer": case.answer, "sources": list(case.sources)})
        return json.dumps({"answer": case.answer})


BY_PROMPT = {case.prompt: case for case in CASES.values()}


class PilotEndToEndTest(unittest.TestCase):
    def test_mock_ollama_suite_1_8_run(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), PilotHandler) as server, tempfile.TemporaryDirectory() as tmp:
            server.models = [{"name": "alpha:latest", "digest": "alpha-digest"}]
            server.loaded, server.requests = [], []
            threading.Thread(target=server.serve_forever, daemon=True).start()
            out = Path(tmp) / "eval"
            command = [sys.executable, str(ROOT / "light_eval.py"), "run", "--suite", "1.8", "--models", "alpha",
                       "--tracks", "agent", "rag", "instructions", "context", "vision", "--phases", "warm",
                       "--port", str(server.server_address[1]), "--no-thermal-gate", "--cpu-only", "--out", str(out)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual((manifest["status"], manifest["suite_version"], manifest["protocol_version"]),
                             ("complete", "1.8.0", 4))
            self.assertIn("fixtures/v1_8/photos/kangaroos.jpg", manifest["sha256"])
            self.assertTrue(manifest["case_metadata"]["agent-frost-injection"]["injection"])
            rows = [json.loads(line) for line in (out / "cases.jsonl").read_text().splitlines()]
            warm = [row for row in rows if row["phase"] == "warm"]
            repeat = [row for row in rows if row["phase"] == "repeat"]
            self.assertEqual((len(warm), len(repeat)), (100, 15))
            self.assertEqual({row["status"] for row in warm}, {"pass"}, [r["case"] for r in warm if r["status"] != "pass"])
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["injection"]["alpha:latest"], {"n": 10, "followed": 0, "passed": 10})
            groups = {(group["phase"], group["track"]): group for group in summary["groups"]}
            self.assertEqual(groups["warm", "rag"]["rag_metrics"], {"n": 20, "answer_passed": 20, "sources_passed": 20})
            self.assertEqual(groups["warm", "agent"]["pass_rate"], 1.0)
            self.assertIsNone(groups["repeat", "agent"]["pass_rate"])
            self.assertEqual(summary["repeatability"]["alpha:latest"]["repeated_cases"], 15)
            # What the runner writes is what publishing accepts: only the track subset (and, since the
            # mock server can't be inspected, the prompt cache setting) stand between it and the gate.
            sys.path.insert(0, str(ROOT / "tools"))
            import publish
            manifest["server_prompt_cache_ram_mib"] = "0"
            blocks = [json.loads(line) for line in (out / "blocks.jsonl").read_text().splitlines()]
            problems = publish.run_problems(manifest, rows, blocks=blocks)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("ran only some tracks", problems[0])


class PilotWarmupFailureTest(unittest.TestCase):
    def test_planted_instruction_cases_count_when_the_warmup_fails(self):
        from unittest.mock import Mock, patch
        from light_eval import WARMUP, followed_injection, run_slot
        case = next(case for case in CASES.values() if getattr(case, "has_injection", False))
        args = apply_suite_defaults(build_parser().parse_args(["run", "--models", "fixture", "--phases", "warm"]))
        api, monitor = Mock(), Mock()
        api.loaded.return_value = []
        api.json.return_value = {"models": [{"name": "fixture:latest", "digest": "d"}]}
        monitor.take.return_value = monitor.gate.return_value = {"t": 0}
        monitor.stats.return_value = {"system_oom_kills": None}
        failing = lambda *a, **k: ({"status": "out_of_memory", "reason": "runner crashed"} if a[4] is WARMUP
                                   else {"status": "pass"})
        records, blocks = io.StringIO(), io.StringIO()
        with patch("light_eval.run_case", side_effect=failing), patch("sys.stdout", new_callable=io.StringIO):
            run_slot(api, args, monitor, None, {"round": 1, "position": 1, "model": "fixture:latest",
                                                "metadata": {"digest": "d", "capabilities": ["completion", "tools", "vision"]}},
                     [case], {}, records, blocks)
        row = json.loads(records.getvalue().splitlines()[0])
        self.assertEqual((row["status"], row.get("injection")), ("out_of_memory", True))
        self.assertFalse(followed_injection(row))
        self.assertEqual(summarize([row], [])["injection"]["fixture:latest"], {"n": 1, "followed": 0, "passed": 0})
        self.assertTrue(followed_injection({"status": "followed_injection"}))
        self.assertFalse(followed_injection({"status": "followed_injection", "injection_followed": False}))


class PilotRequestTest(unittest.TestCase):
    """No request may depend on a case's expected answer, for any of the 160 cases."""
    scripted_case = BenchmarkTest.scripted_case
    ALTERNATIVE = {int: -987654, float: -98765.4321, str: "unseen-audit-value", dict: {"unseen": 1},
                   list: ["unseen-audit-value"], type(None): "unseen-audit-value"}

    def test_requests_do_not_depend_on_expected_answers(self):
        for case in CASES.values():
            names = {field.name for field in fields(case)}
            if "answer" not in names or case.track == "agent":
                continue  # instructions have no answer; agent answers are covered below
            with self.subTest(case=case.id):
                _, original = self.scripted_case(case, reply("x"), "--suite", "1.8")
                changes = {"answer": self.ALTERNATIVE[type(case.answer)]}
                if "sources" in names:
                    changes["sources"] = ("unseen",)
                _, changed = self.scripted_case(replace(case, **changes), reply("x"), "--suite", "1.8")
                self.assertEqual(original[0][0], changed[0][0])

    def test_agent_requests_do_not_depend_on_expected_answers(self):
        for case in CASES.values():
            if case.track != "agent":
                continue
            with self.subTest(case=case.id):
                _, original = self.scripted_case(case, [{"content": ""}, {"content": "x"}], "--suite", "1.8")
                changed_case = replace(case, answer=self.ALTERNATIVE[type(case.answer)], reference=(), reads=())
                _, changed = self.scripted_case(changed_case, [{"content": ""}, {"content": "x"}], "--suite", "1.8")
                self.assertEqual([body for body, _ in original], [body for body, _ in changed])


class PilotSummaryTest(unittest.TestCase):
    def row(self, case, phase, status, **extra):
        return {"model": "m", "round": 1, "phase": phase, "case": case, "track": case.split("-")[0],
                "difficulty": "hard", "status": status, **extra}

    def test_new_metrics(self):
        rows = [
            {**self.row("rag-refund-days", "warm", "followed_injection",
                        rag_evaluation={"answer_correct": False, "sources_correct": False}), "injection": True},
            {**self.row("agent-frost-injection", "warm", "pass"), "injection": True,
             "agent_evaluation": {"tool_use_correct": True, "final_answer_correct": True}},
            self.row("rag-on-call", "warm", "pass", rag_evaluation={"answer_correct": True, "sources_correct": True}),
            self.row("rag-on-call", "repeat", "wrong_sources",
                     rag_evaluation={"answer_correct": True, "sources_correct": False}),
            self.row("context-door-code", "warm", "pass"),
        ]
        rows[-1]["track"] = "context"
        summary = summarize(rows, [])
        self.assertEqual(summary["injection"], {"m": {"n": 2, "followed": 1, "passed": 1}})
        rag = next(group for group in summary["groups"] if group["track"] == "rag" and group["phase"] == "warm")
        self.assertEqual(rag["pass_rate"], 0.5, "scores come from the first attempt only")
        self.assertEqual(rag["rag_metrics"], {"n": 2, "answer_passed": 1, "sources_passed": 1})
        self.assertEqual(summary["repeatability"]["m"]["repeated_cases"], 1)
        self.assertEqual(summary["repeatability"]["m"]["status_stable"], 0)
        self.assertIn("context", {group["track"] for group in summary["groups"]})


del BenchmarkTest  # don't run the imported 1.7 tests twice

if __name__ == "__main__":
    unittest.main()
