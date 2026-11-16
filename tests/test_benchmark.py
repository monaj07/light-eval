import base64
import io
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import permutations
from pathlib import Path
from unittest.mock import patch

from cases import ROOT, Case, dataset, dispatch, score
from light_eval import rounds
from runtime import BenchError, Ollama, Telemetry, range_stats

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
        prompt = request["messages"][0]["content"].split("\n\n", 1)[1]
        case = CASES.get(prompt)
        if case and case.image:
            images = request["messages"][0]["images"]
            assert base64.b64decode(images[0]) == (ROOT / "fixtures" / case.image).read_bytes()
        if case and case.track == "agent":
            assert len(request["tools"]) == 4
            results = [msg for msg in request["messages"] if msg["role"] == "tool"]
            if case.calls[:len(results)]:
                assert [msg["tool_name"] for msg in results] == [name for name, _ in case.calls[:len(results)]]
            if len(results) < len(case.calls):
                name, args = case.calls[len(results)]
                message = {"tool_calls": [{"function": {"name": name, "arguments": args}}]}
            else:
                message = {"content": json.dumps(case.answer)}
        else:
            message = {"content": json.dumps(case.answer) if case else '"ready"'}
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
        self.assertEqual(score(arithmetic, "1.0", [], {}), "pass")
        self.assertEqual(score(arithmetic, "true", [], {}), "wrong_answer")
        self.assertEqual(score(arithmetic, "NaN", [], {}), "invalid_json")
        self.assertEqual(score(arithmetic, "1e9999", [], {}), "invalid_json")
        structured = Case("test", "structured", "", {"count": 1})
        self.assertEqual(score(structured, '{"count":1.0}', [], {}), "wrong_answer")
        self.assertEqual(score(structured, '{"count":1,"count":2}', [], {}), "invalid_json")
        with self.assertRaises(ValueError):
            dispatch("calculate", {"operation": "add", "a": True, "b": 2}, {})

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
                           "--port", port, "--no-thermal-gate", "--sustain-seconds", ".025", "--out", str(out)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=25, check=False)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                manifest = json.loads((out / "manifest.json").read_text())
                self.assertEqual(manifest["status"], "complete")
                self.assertIsNone(manifest["temperature_band_c"])
                self.assertEqual(len(manifest["plan"]), 2)
                self.assertEqual(server.loaded, [])
                self.assertTrue(all(req["options"]["num_gpu"] == 0 for req in server.requests))
                self.assertTrue(all(req["think"] is False for req in server.requests))
                summary = json.loads((out / "summary.json").read_text())
                rows = {(r["model"], r["phase"], r["track"]): r for r in summary["groups"]}
                self.assertEqual(rows["alpha:latest", "warm", "agent"]["pass_rate"], 1.0)
                self.assertEqual(rows["alpha:latest", "warm", "vision"]["n"], 12)
                self.assertEqual(rows["beta:latest", "warm", "vision"]["outcomes"], {"unsupported": 12})
                self.assertEqual(rows["alpha:latest", "warm", "context-long"]["outcomes"], {"pass": 6})
                self.assertEqual(rows["alpha:latest", "cold", "performance"]["load_s"]["median"], .05)
                self.assertEqual(summary["repeatability"]["alpha:latest"]["raw_output_stable"], 31)
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
