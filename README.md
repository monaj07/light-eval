# Light Eval

Light Eval compares local Ollama models on a Raspberry Pi 5. The runner uses Python 3.10+ and the standard library. It needs no API key or Python packages. Run it on the Pi itself so latency, memory, and temperature come from the same machine.

## Quick start

On the Pi, start Ollama as usual and check which models are available locally.

```bash
cd ~/light-eval
ollama list
ollama ps
```

Stop any models shown by `ollama ps` using `ollama stop MODEL`. Keep other Ollama clients idle during benchmarking. Replace the model tags in the examples with tags from your own `ollama list`. Use local weights and disable Ollama cloud inference on the server for an offline guarantee. [Ollama cloud settings](https://docs.ollama.com/cloud)

Preview the balanced schedule without connecting to Ollama.

```bash
python3 light_eval.py run --models gemma4:e2b qwen3.5:2b qwen3.5:4b --plan
```

Run the smoke test with one model. The thermal gate is optional here so the same command can exercise a local Ollama installation on a Mac.

```bash
python3 light_eval.py run --models gemma4:e2b --phases warm \
  --tracks arithmetic --no-thermal-gate --out results/smoke
```

On the Pi, run all phases and tracks. If you omit `--out`, each invocation creates a timestamped directory under `results/`. The output directory must be new.

```bash
python3 light_eval.py run --models gemma4:e2b qwen3.5:2b qwen3.5:4b \
  --out results/pi5-baseline
python3 light_eval.py report results/pi5-baseline
```

For a publishable comparison, add `--setup "…"` with your cooling and fan setup, power supply, storage, ambient conditions if known, and any nondefault Ollama service settings. Keep those conditions fixed. Record the server settings separately if they differ from the runner defaults.

## What gets measured

| Track | Cases | Scoring |
| --- | ---: | --- |
| Arithmetic | 8 | JSON numbers, with an explicit tolerance for the approximate fraction |
| Structured output | 6 | Typed JSON values, exact requested fields, and array order |
| Agentic tools | 6 | Final answer, call names and arguments in order, and simulated final state where applicable |
| Vision | 6 | Presence, counting, and OCR against four bundled synthetic PNGs |
| Context | 5 | Exact retrieval at different positions in short and longer dispatch logs |

The arithmetic and agent tracks both include `173 × 29`. The agent case requires a calculator call. Tools are deterministic fixtures with state reset for each case. They do no I/O with hardware, files, or external services. Strict JSON scoring rewards answers in the requested format and reports format errors separately from wrong answers. Each capability-dependent case is marked `unsupported` when the capability is absent from Ollama metadata. API errors and timeouts on supported cases count against pass rate.

| Phase | What the runner does |
| --- | --- |
| Cold | Checks that Ollama has no resident model, then times the first text request and records model load time. The operating-system page cache is retained and uncontrolled, so this measures an unloaded-model request. |
| Warm | Warms up with a separate prompt, then benchmarks the selected quality cases with the model resident. Each measured case starts inside the thermal band on the Pi. |
| Sustained | Starts with a warmed model inside the same thermal band and runs sequential decode requests for at least 90 seconds by default. The final request can finish after the window. The report shows actual duration and first-versus-last-third decode speed when at least three requests complete. |

## Fairness and repeatability

- One cycle gives each model every run position exactly once. With three models, that means three rounds and nine model slots. Cycles are paired: the second cycle reverses the cyclic ordering, so for each leading model the remaining models run in reverse order. With three models, `--cycles 2` guarantees all six possible model orders exactly once, for any seed; each additional pair of cycles repeats that guarantee. Models see the same case order within each round.
- The default thermal band is set from the idle temperature, from 4°C below to 3°C above baseline, with a 65°C ceiling. `--temp-min` and `--temp-max` let you set an explicit band. The runner checks Pi temperature and current `vcgencmd` flags at each gate. It records temperature, memory, swap, and throttling throughout measured workloads. A gate that cannot be reached within `--cooldown` seconds ends the run with partial results. [Raspberry Pi diagnostics](https://www.raspberrypi.com/documentation/computers/os.html#vcgencmd)
- Defaults request CPU inference, two threads, a 4,096-token context, temperature zero, and seed 42. The runner uses native model templates and records their hashes and the installed model digests. The shared generation cap is 128 tokens per model turn. A 90-second case budget covers all turns. `--thinking off` requests disabled thinking for models that advertise it. Use `--thinking default` to run with each model's default thinking behavior.
- Distinct case references early in the prompt reduce full prompt-cache reuse. Ollama can still reuse shared prefixes and agent conversation history. Cached token counts are logged when Ollama returns them. The context cases keep the underlying prompt identical across models and lengths are reported in each model's own tokens. Reducing `--context` can change whether the full longer prompt fits.
- Repeatability reports how many cases kept the same scored outcome and how many with complete responses produced identical streamed content, thinking, and tool calls across rounds. The benchmark uses 31 hand-authored cases and four synthetic images. Use its results for the tested workloads.

## Reading the results

| File | Contents |
| --- | --- |
| `manifest.json` | Options, seed, schedule, hardware and software identifiers, model digests, thermal band, source and image hashes, and run status |
| `cases.jsonl` | One row per case with outcome, wall time, time to first streamed output, time to first text chunk, per-turn Ollama durations and token counts, response content, tool trace, and resource samples summarized over that case |
| `blocks.jsonl` | Per-model and per-phase runtime, residency information, and resource measurements |
| `telemetry.jsonl` | Timestamped raw readings for temperature, throttling flags, system memory, swap I/O, and Linux OOM counter |
| `summary.json` | Track-level success counts, latency and decode distributions, output repeatability, and sustained decode comparisons |

Time to first token in the summary means time to the first nonempty streamed text, thinking, or tool event. The benchmark also records first text separately. Decode tokens/s is calculated from Ollama's generation count and duration. Token units vary across models, so compare end-to-end time and task success alongside tokens/s. Timing distributions use completed responses, with outcome counts and timing sample sizes shown alongside them. p95 is reported from 20 or more measurements using the nearest rank. Memory is a sampled system-wide estimate from Linux `MemTotal - MemAvailable`. This includes background processes. Swap I/O deltas are in pages, with page size in the manifest. [Ollama chat API](https://docs.ollama.com/api/chat)

Run the automated checks with the command below. They use a mock Ollama server and can be run on the Pi or Mac without installed models.

```bash
python3 -m unittest discover -s tests -v
```

If pytest is installed, you can also run the tests from the project root:

```bash
pytest tests/test_benchmark.py
```
