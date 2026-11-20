"""Loopback Ollama transport and passive Mac/Linux telemetry."""
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from functools import lru_cache

from cases import parse_json

METRIC_FIELDS = (
    "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_cached_count",
    "prompt_eval_duration", "eval_count", "eval_duration", "done_reason",
)
CPU_TEMP = Path("/sys/class/thermal/thermal_zone0/temp")


class BenchError(Exception):
    def __init__(self, message, status="protocol_error"):
        super().__init__(message)
        self.status = status


class Ollama:
    def __init__(self, port):
        self.port = port

    def request(self, endpoint, body=None, *, timeout=15, stream=False):
        start = time.monotonic()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        expired = threading.Event()

        def interrupt(sock):
            expired.set()
            try:
                sock.shutdown(2)
            except OSError:
                pass

        timer = None
        try:
            conn.connect()
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError
            timer = threading.Timer(remaining, interrupt, args=(conn.sock,))
            timer.daemon = True
            timer.start()
            data = None if body is None else json.dumps(body).encode()
            conn.request("GET" if data is None else "POST", "/api/" + endpoint, data,
                         {"Content-Type": "application/json"})
            with conn.getresponse() as response:
                if response.status != 200:
                    detail = response.read(8192).decode(errors="replace")
                    raise api_error(detail, response.status)
                for line in response if stream else (response.read(),):
                    if expired.is_set():
                        raise TimeoutError
                    if line.strip():
                        try:
                            record = parse_json(line.decode())
                        except (ValueError, RecursionError) as exc:
                            raise BenchError("Ollama returned invalid JSON") from exc
                        if not isinstance(record, dict):
                            raise BenchError("Expected an Ollama response object")
                        if "error" in record:
                            raise api_error(str(record["error"]))
                        yield time.monotonic() - start, record
                if expired.is_set():
                    raise TimeoutError
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            if expired.is_set() or isinstance(exc, TimeoutError):
                raise BenchError(f"Ollama request exceeded {timeout:.2f}s", "timeout") from exc
            raise BenchError(str(exc), "connection_error") from exc
        finally:
            if timer is not None:
                timer.cancel()
            conn.close()

    def json(self, endpoint, body=None, timeout=15):
        rows = list(self.request(endpoint, body, timeout=timeout))
        if not rows:
            raise BenchError(f"Empty Ollama response from {endpoint}")
        return rows[0][1]

    def loaded(self):
        return self.json("ps")["models"]

    def unload(self, model):
        if any(item.get("name", item.get("model")) in {model, model.removesuffix(":latest")}
               for item in self.loaded()):
            self.json("generate", {"model": model, "keep_alive": 0, "stream": False})
        deadline = time.monotonic() + 10
        while self.loaded():
            if time.monotonic() >= deadline:
                raise BenchError("Ollama still has resident models. Check for other Ollama clients.")
            time.sleep(.2)

    def chat(self, body, timeout, turn):
        for elapsed, chunk in self.request("chat", body, stream=True, timeout=timeout):
            message = chunk.get("message") or {}
            if not isinstance(message, dict):
                raise BenchError("Ollama returned an invalid chat message")
            content = message.get("content") or ""
            thinking = message.get("thinking") or ""
            calls = message.get("tool_calls") or []
            if not isinstance(content, str) or not isinstance(thinking, str) or not isinstance(calls, list):
                raise BenchError("Ollama returned invalid chat fields")
            if (content or thinking or calls) and turn["first_output_s"] is None:
                turn["first_output_s"] = elapsed
            if content and turn["first_text_s"] is None:
                turn["first_text_s"] = elapsed
            turn["content"] += content
            turn["thinking"] += thinking
            turn["tool_calls"].extend(calls)
            if chunk.get("done"):
                turn["metrics"] = {key: chunk[key] for key in METRIC_FIELDS if key in chunk}
                turn["response_s"] = elapsed
                return
        raise BenchError("Ollama stream ended before a final response")


def api_error(detail, code=None):
    detail = detail.strip()
    lower = detail.lower()
    oom = ("out of memory", "unable to allocate", "failed to allocate", "more system memory",
           "failed to reserve memory", "not enough memory", "available memory")
    status = "out_of_memory" if any(phrase in lower for phrase in oom) else "api_error"
    return BenchError(f"HTTP {code}: {detail}" if code is not None else detail, status)


def read_text(path):
    try:
        return Path(path).read_text().strip().strip("\x00")
    except OSError:
        return None


def linux_fields(path, keys):
    raw = read_text(path) or ""
    values = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) >= 2 and (key := fields[0].rstrip(":")) in keys:
            values[key] = int(fields[1])
    return values


def command_output(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1, check=False)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def server_setting(key):
    """Return an environment setting of the running Ollama server.

    Ollama's llama-server runners inherit the server's environment, so this is
    the value they use. Returns "unset" when the server has no such variable and
    None when the server environment can't be read.
    """
    pids = command_output(["pgrep", "-f", "ollama serve"]).split()
    if platform.system() == "Darwin":
        sources = [command_output(["ps", "-E", "-ww", "-p", pid, "-o", "command="]) for pid in pids]
    else:
        sources = [(read_text(f"/proc/{pid}/environ") or "").replace("\x00", " ") for pid in pids]
        sources.append(command_output(["systemctl", "show", "ollama", "-p", "Environment", "--value"]))
    for text in sources:
        if match := re.search(rf"(?:^|\s){key}=(\S*)", text):
            return match[1]
    return "unset" if any(text.strip() for text in sources) else None


@lru_cache(maxsize=1)
def mac_total_memory_kib():
    raw = command_output(["sysctl", "-n", "hw.memsize"]).strip()
    return int(raw) // 1024 if raw.isdigit() else None


def mac_memory():
    raw = command_output(["vm_stat"])
    page_match = re.search(r"page size of (\d+) bytes", raw)
    if not page_match:
        return {}
    page_bytes = int(page_match[1])
    fields = {key.strip().strip('"'): int(value) for key, value in
              re.findall(r"^([^:\n]+):\s+(\d+)\.?\s*$", raw, re.MULTILINE)}

    def kib(key):
        return fields[key] * page_bytes // 1024 if key in fields else None

    swap_raw = command_output(["sysctl", "-n", "vm.swapusage"])
    swap_match = re.search(r"used\s*=\s*([\d.]+)([KMGT])", swap_raw)
    swap_used = float(swap_match[1]) * {"K": 1, "M": 1024, "G": 1024**2, "T": 1024**3}[swap_match[2]] if swap_match else None
    pressure = command_output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    return {"MemTotal": mac_total_memory_kib(), "MemFree": kib("Pages free"),
            "memory_page_bytes": page_bytes,
            "memory_compressed_kib": kib("Pages occupied by compressor"),
            "memory_wired_kib": kib("Pages wired down"), "SwapUsed": swap_used,
            "memory_pressure_level": int(pressure) if pressure.isdigit() else None,
            "pswpin": fields.get("Swapins"), "pswpout": fields.get("Swapouts")}


def sample():
    temperature = read_text(CPU_TEMP)
    throttled = None
    if shutil.which("vcgencmd"):
        try:
            result = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                                    text=True, timeout=1, check=False)
            match = re.search(r"throttled=(0x[0-9a-fA-F]+)", result.stdout)
            if match:
                throttled = int(match[1], 16)
        except (OSError, subprocess.TimeoutExpired):
            pass
    if platform.system() == "Darwin":
        mem, vm = mac_memory(), {}
    else:
        mem = linux_fields("/proc/meminfo", {"MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"})
        if "SwapTotal" in mem and "SwapFree" in mem:
            mem["SwapUsed"] = mem["SwapTotal"] - mem["SwapFree"]
        mem["memory_page_bytes"] = os.sysconf("SC_PAGE_SIZE")
        # zram keeps swapped pages compressed in RAM. mm_stat's third field is the RAM it uses,
        # which lets memory in use count those pages once rather than twice.
        zram = [read_text(path) for path in sorted(Path("/sys/block").glob("zram*/mm_stat"))]
        zram = [int(text.split()[2]) for text in zram if text and len(text.split()) > 2]
        if zram:
            mem["zram_ram_kib"] = sum(zram) // 1024
        vm = linux_fields("/proc/vmstat", {"pswpin", "pswpout", "oom_kill"})
    return {"t": time.monotonic(), "cpu_c": float(temperature) / 1000 if temperature else None,
            "throttled_bits": throttled, **mem, **vm}


def range_stats(rows):
    def values(key):
        return [row[key] for row in rows if row.get(key) is not None]

    temperatures = values("cpu_c")
    flags = values("throttled_bits")
    active_flags = 0
    for flag in flags:
        active_flags |= flag & 0xffff
    new_sticky = (flags[-1] & ~flags[0]) >> 16 if flags else None
    available = values("MemAvailable")
    total = values("MemTotal")

    def delta(key):
        points = values(key)
        return points[-1] - points[0] if len(points) >= 2 else None

    def swap_mib(key):
        pages = delta(key)
        sizes = values("memory_page_bytes")
        return pages * sizes[0] / 2**20 if pages is not None and sizes and len(set(sizes)) == 1 else None

    return {
        "samples": len(rows),
        "start_cpu_c": temperatures[0] if temperatures else None,
        "peak_cpu_c": max(temperatures, default=None),
        "mean_cpu_c": sum(temperatures) / len(temperatures) if temperatures else None,
        "end_cpu_c": temperatures[-1] if temperatures else None,
        "min_mem_available_kib": min(available, default=None),
        "peak_system_used_estimate_kib": total[0] - min(available) if total and available else None,
        "min_free_memory_kib": min(values("MemFree"), default=None),
        "peak_compressed_memory_kib": max(values("memory_compressed_kib"), default=None),
        "peak_swap_used_kib": max(values("SwapUsed"), default=None),
        # RAM in use plus swapped pages, sample by sample, minus the RAM zram uses to hold them.
        "peak_memory_in_use_kib": max((row["MemTotal"] - row["MemAvailable"] + (row.get("SwapUsed") or 0)
                                       - (row.get("zram_ram_kib") or 0)
                                       for row in rows if row.get("MemTotal") and row.get("MemAvailable") is not None
                                       and ("zram_ram_kib" in row or not row.get("SwapUsed"))), default=None),
        "swap_in_pages": delta("pswpin"), "swap_out_pages": delta("pswpout"),
        "swap_in_mib": swap_mib("pswpin"), "swap_out_mib": swap_mib("pswpout"),
        "system_oom_kills": delta("oom_kill"),
        "active_throttling_bits_or": active_flags if flags else None,
        "new_throttling_history_bits": new_sticky,
    }


class Telemetry:
    def __init__(self, output, interval):
        self.output = output
        self.interval = interval
        self.rows = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.poll, daemon=True)
        self.gate_timeouts = []  # gates that ran out of time; the run continues and records them

    def __enter__(self):
        self.take()
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()

    def take(self):
        with self.lock:
            row = sample()
            self.rows.append(row)
            self.output.write(json.dumps(row) + "\n")
            self.output.flush()
        return row

    def poll(self):
        while not self.stop.wait(self.interval):
            self.take()

    def stats(self, start):
        end = self.take()
        with self.lock:
            subset = [row for row in self.rows if start["t"] <= row["t"] <= end["t"]]
        return range_stats(subset)

    def gate(self, band, wait):
        if band is None:
            return self.take()
        low, high = band
        deadline = time.monotonic() + wait
        while True:
            row = self.take()
            temp, flags = row["cpu_c"], row["throttled_bits"]
            if temp is None or flags is None:
                raise BenchError("Pi temperature or vcgencmd unavailable. Use --no-thermal-gate for a smoke run.")
            if low <= temp <= high and flags & 0xffff == 0:
                return row
            if time.monotonic() >= deadline:
                # Waiting longer rarely helps, and stopping would lose an unattended run,
                # so continue and record the miss for the manifest.
                self.gate_timeouts.append({"cpu_c": temp, "band_c": [low, high], "throttled_bits": flags,
                                           "waited_s": wait})
                print(f"light-eval: warning: temperature gate timed out at {temp:.1f}°C "
                      f"(band {low:.1f}-{high:.1f}°C, flags {flags:#x}); continuing", file=sys.stderr, flush=True)
                return row
            time.sleep(min(2, max(0, deadline - time.monotonic())))
