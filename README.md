# Light Eval

Light Eval compares local Ollama models on a Raspberry Pi 5. The runner uses Python 3.10+ and the standard library. It needs no API key or Python packages. Run it on the Pi itself so latency, memory, and temperature come from the same machine.

## Quick start

Install Ollama and make the models available locally. You can use your usual Ollama service to check them.

```bash
cd ~/light-eval
ollama list
ollama ps
```

Stop any models shown by `ollama ps` using `ollama stop MODEL`. Keep other Ollama clients idle during benchmarking. Replace the model tags in the examples with tags from your own `ollama list`.

The evaluator connects to your existing local Ollama server. It does not start servers, change service settings, or stop because macOS reports memory pressure. Use local weights and keep other Ollama clients idle.

Preview the balanced schedule without connecting to Ollama.

```bash
python3 light_eval.py run --models gemma4:e2b qwen3.5:2b qwen3.5:4b --plan
```

Run the smoke test with one model. The thermal gate is optional here so the same command can exercise a local Ollama installation on a Mac.

```bash
python3 light_eval.py run --models gemma4:e2b --phases warm \
  --tracks arithmetic --no-thermal-gate --out results/smoke
```

For each model slot, the script loads the model and runs one short warmup, keeps it resident for all selected warm cases, then unloads it at the end. All 45 cases therefore share one load and warmup in a single-model, single-cycle warm run. Loading and warmup are outside the measured case timings. After an OOM or unexpected eviction, the script recovers and warms the model before timing the next case, and records the extra warmup. The failed case retains its original result.

On Mac, the script lets Ollama select its normal accelerator (Metal on supported Macs). On the Pi it uses CPU inference. Add `--cpu-only` if you specifically need CPU-only Mac timings; accelerated Mac timings are not Pi CPU timings. The execution setting is recorded in the manifest.

Memory and swap are recorded as passive telemetry. The script does not abort on a pressure warning or attempt to clear other applications' memory. Unloading at the end releases the model and its retained prompt cache. The cache can grow during a slot, so unloading afterward cannot prevent paging that has already occurred while the tests were running.

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
| Arithmetic | 8 | Numbers in an enforced JSON `answer` object, with an explicit tolerance for the approximate fraction |
| Structured output | 6 | Typed JSON values, exact requested fields, and array order |
| Agentic tools | 12 | Six controls and six challenges; separate tool-use and exact-answer scores, plus strict end-to-end success |
| Vision | 6 | Presence, counting, and OCR against four bundled synthetic PNGs |
| Context | 13 | Five retrieval controls and eight challenges covering revisions, joins, filtering/counting, and absent evidence |

Arithmetic, vision, and context requests enforce a JSON object with exactly one key, `answer`, through Ollama's `format` schema. The value has the appropriate number, boolean, or string type, for example `{"answer":3.6}` or `{"answer":false}`. Prompts request the same object format, and the scorer extracts the value before checking correctness. Schemas contain no expected values or answer choices. Using an object avoids the answer-quality regression observed with Gemma E2B when enforcing bare-number output. The structured-output track uses prompt instructions without an enforced schema so it continues to test the model's ability to produce the requested structure. Responses are still scored for correctness; schema enforcement does not prevent wrong answers, token limits, or API failures. [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)

The arithmetic and agent tracks both include `173 × 29`. The agent case requires a calculator call. Tools are deterministic fixtures with state reset for each case. They do no I/O with hardware, files, or external services. Agents use native tool calls without a final-answer schema during tool selection. Once the model returns a response without tool calls, the runner includes that response in the conversation and requests one schema-constrained final `answer` object with tools removed. This avoids combining a final-answer grammar with native tool calling, which can suppress tool calls in Ollama. The extra request shares the case deadline and its tokens and latency are included in case metrics. Tool calls, arguments, order, and final state are still scored. [Ollama tool/format compatibility issue](https://github.com/ollama/ollama/issues/18944)

Agent results report three measures. **Tool-use success** requires the expected calls, arguments, and order, plus the correct simulated state where applicable; the no-tool control requires a completed response without tool calls. **Exact-answer success** checks only the final JSON answer value. **End-to-end success** requires both. Correct tool use receives credit even when the final answer is a sentence instead of the requested short value. Invalid tool attempts and tool-budget violations fail tool-use scoring, and supported runtime failures stay in the denominator. Agent prompts explicitly request only the status field value or color/state word, without a sentence. No substring matching or model-based judging is used to accept verbose answers.

The agent challenges add intermediate calculator results, decisions based on order status, stopping after a missing-customer result, reading state before changing it, avoiding a redundant write, and a six-call workflow involving two customers and final-state verification. A deterministic `read_indicator` fixture is available only in the state-reading challenges; controls retain their original four tools. Each case resets its simulated state, including an already-green case that requires no write. New prompts specify the required sequence so exact trace scoring does not reject alternative plans that the prompt left open. Native tool-call IDs, when supplied, are returned with their tool results.

Context challenges use both 16-record short logs and 96-record long logs. They ask for the highest approved revision despite later pending/cancelled records, resolve an alias through two cross-references, count records satisfying several exact conditions, and distinguish missing assignments from similar IDs and non-authoritative comments. Inputs and targets are fixed by case ID and suite version, identically for every model. The original 8- and 90-record retrieval cases remain as controls. Context requests disable automatic prompt truncation: an input that does not fit the selected `--context` fails explicitly rather than silently losing evidence.

Reports show `control` and `challenge` success counts separately when both are present. `cases.jsonl` stores the difficulty label and `summary.json` includes `by_difficulty` breakdowns. This distinguishes success on the original controls from performance on the new workloads; a harder design does not guarantee a particular failure rate.

Each capability-dependent case is marked `unsupported` when the capability is absent from Ollama metadata. API errors and timeouts on supported cases count against pass rate. Suite version 1.1 introduced enforced scalar answers; version 1.2 replaced them with `answer` objects and matching prompts and scoring. Version 1.3 added separate agent metrics and clarified the answer instructions. **Version 1.4** retains the original cases, adds agent/context challenges and difficulty reporting, and raises the default tool budgets to support the longer plans. **Version 1.4.3** uses the existing Ollama server, resets the model between independent warm cases, allows native Mac acceleration, and uses a smaller processing batch. Mac memory telemetry is passive. **Version 1.4.4** keeps each model resident throughout its warm suite, with one warmup per slot and unloading afterward. **Version 1.4.5** explicitly recovers after OOM even if Ollama still lists the crashed runner, keeping reload time outside the next case's deadline. Cases and scoring remain the same as 1.4. Compare models using the same suite version and settings. Existing result files retain their original scores; rerun into a new output directory to evaluate the new protocol. Reports of older agent records without separate checks display `tool-use checks unavailable` rather than inferring new scores from today's fixtures.

The object format was chosen after inspecting responses on these fixtures. Treat the existing results as exploratory benchmark development. For an independent assessment, freeze the protocol and evaluate additional cases that were not used to choose it.

| Phase | What the runner does |
| --- | --- |
| Cold | Checks that Ollama has no resident model, then times the first text request and records model load time. The operating-system page cache is retained and uncontrolled, so this measures an unloaded-model request. |
| Warm | Loads and warms the model once per slot, runs all selected cases while it stays resident, then unloads it. Each measured case starts inside the thermal band on the Pi. |
| Sustained | Starts with a warmed model inside the same thermal band and runs sequential decode requests for at least 90 seconds by default. The final request can finish after the window. The report shows actual duration and first-versus-last-third decode speed when at least three requests complete. |

## Fairness and repeatability

- One cycle gives each model every run position exactly once. With three models, that means three rounds and nine model slots. Cycles are paired: the second cycle reverses the cyclic ordering, so for each leading model the remaining models run in reverse order. With three models, `--cycles 2` guarantees all six possible model orders exactly once, for any seed; each additional pair of cycles repeats that guarantee. Models see the same case order within each round.
- The default thermal band is set from the idle temperature, from 4°C below to 3°C above baseline, with a 65°C ceiling. `--temp-min` and `--temp-max` let you set an explicit band. The runner checks Pi temperature and current `vcgencmd` flags at each gate. It records temperature, memory, swap, and throttling throughout measured workloads. A gate that cannot be reached within `--cooldown` seconds ends the run with partial results. [Raspberry Pi diagnostics](https://www.raspberrypi.com/documentation/computers/os.html#vcgencmd)
- Defaults use Ollama backend selection on Mac and CPU inference on the Pi, with two CPU threads, a 4,096-token context, a 64-token prompt-processing batch, temperature zero, and seed 42. The smaller batch limits processing workspace without shortening the input or output budget. The runner uses native model templates and records their hashes and the installed model digests. The shared generation cap is 128 tokens per model turn. A 600-second (10-minute) case budget covers all turns; override it with `--timeout SECONDS`. Agent budgets default to six tool-call turns and eight calls; lowering them can make the longer challenges impossible to complete. `--thinking off` requests disabled thinking for models that advertise it. Use `--thinking default` to run with each model's default thinking behavior, and increase `--max-tokens` as needed (for example, to 1024): the token budget includes both thinking and the final answer.
- Distinct case references early in the prompt reduce full prompt-cache reuse. The model and its native prompt cache remain resident across all warm cases within a slot, and are released when the slot ends. The sustained phase keeps the model resident and repeats one prompt reference for its continuous decode workload, allowing native prefix reuse instead of filling the cache with distinct references. Cached token counts are logged when Ollama returns them. The context cases keep the underlying prompt identical across models and lengths are reported in each model's own tokens. Reducing `--context` can change whether the full longer prompt fits.
- Repeatability reports how many cases kept the same scored outcome and how many with complete responses produced identical streamed content, thinking, and tool calls across rounds. The benchmark uses 45 deterministic cases (31 retained controls and 14 challenges) and four synthetic images. Use its results for the tested workloads.

## Reading the results

| File | Contents |
| --- | --- |
| `manifest.json` | Suite version, options, seed, schedule, case difficulty and offered tools, response schemas, agent final-answer policy, hardware and software identifiers, model digests, thermal band, source and image hashes, and run status |
| `cases.jsonl` | One row per case with outcome, difficulty, response schema, agent tool-use and exact-answer checks, wall time, time to first streamed output, time to first text chunk, per-turn format constraints, Ollama durations and token counts, response content, tool trace, and resource samples summarized over that case |
| `blocks.jsonl` | Per-model and per-phase runtime, residency information, and resource measurements |
| `telemetry.jsonl` | Timestamped temperature and throttling where available, Linux memory/OOM readings, Mac free/compressed/wired memory, and system-wide swap usage and I/O |
| `summary.json` | Track-level and difficulty-level success counts, separate agent tool-use and exact-answer totals and rates, latency and decode distributions, output repeatability, and sustained decode comparisons |

Time to first token in the summary means time to the first nonempty streamed text, thinking, or tool event. The benchmark also records first text separately. Decode tokens/s is calculated from Ollama's generation count and duration. Token units vary across models, so compare end-to-end time and task success alongside tokens/s. Timing distributions use completed responses, with outcome counts and timing sample sizes shown alongside them. p95 is reported from 20 or more measurements using the nearest rank. The system-used-memory estimate uses Linux `MemTotal - MemAvailable`; Mac free and compressed memory are reported separately rather than treated as equivalent to Linux available memory. All memory and swap readings include background processes. Swap I/O deltas are logged in native pages and MiB, using the measured page size. Existing swap allocation does not by itself establish active paging or identify which application caused it. [Ollama chat API](https://docs.ollama.com/api/chat)

Run the automated checks with the command below. They use a mock Ollama server and can be run on the Pi or Mac without installed models.

```bash
python3 -m unittest discover -s tests -v
```

If pytest is installed, you can also run the tests from the project root:

```bash
pytest tests/test_benchmark.py
```
