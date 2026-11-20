# Methodology

This document describes how Light Eval runs and scores models. The [README](../README.md) has the quick start and the results; [CHANGELOG.md](../CHANGELOG.md) has the suite version history.

## Server setup

In the warm phase, the script loads the model and runs one short warmup, then runs every warm case on that load. Loading and warmup are outside the measured case timings. This needs the server's prompt cache disabled (see Prompt cache below). With the default 8 GiB cache, every distinct case prompt stays in RAM: on a 16 GB Mac, `qwen3.5:4b` grew from 5.4 GB to 11.3 GB over 45 cases and swapped out 6.7 GB. With `LLAMA_ARG_CACHE_RAM=0`, one load of `qwen3.5:4b` ran all 58 cases of suite 1.5 with the runner flat at 3.5–4.6 GB, both with thinking off and over 51 minutes with thinking on, with no swap writes during the thinking-on run. After an out-of-memory error or a failed warmup, the next case gets a fresh load.

### Prompt cache

Ollama starts its `llama-server` runner without `--cache-ram`, so the runner's default of 8 GiB applies. The runner inherits the Ollama server's environment, so you can disable the cache by setting `LLAMA_ARG_CACHE_RAM=0` on the server. Cases are independent, so the cross-request cache only costs memory. Within a case, later turns still reuse the earlier ones from the live slot. Set the variable on the server, not in the shell that runs the benchmark:

```bash
# Raspberry Pi (systemd): add the line under [Service], then restart
sudo systemctl edit ollama        # Environment="LLAMA_ARG_CACHE_RAM=0"
sudo systemctl restart ollama

# Mac app: set it, then quit and reopen Ollama (lasts until reboot)
launchctl setenv LLAMA_ARG_CACHE_RAM 0
```

The manifest records the server's value as `server_prompt_cache_ram_mib`: a number of MiB, `unset` (which means the 8 GiB default), or `null` if it couldn't be read. The runner warns when the value isn't 0. The manifest also records `/proc/swaps` as `swap_devices`. Raspberry Pi OS uses zram, compressed swap held in RAM, so a full swap there means RAM is exhausted, and swap counters measure compression activity rather than disk I/O.

### Accelerators and memory telemetry

On Mac, the script lets Ollama select its normal accelerator (Metal on supported Macs). On the Pi it uses CPU inference. Add `--cpu-only` if you specifically need CPU-only Mac timings; accelerated Mac timings are not Pi CPU timings. The execution setting is recorded in the manifest.

Memory and swap are recorded as passive telemetry. The script does not abort on a pressure warning or attempt to clear other applications' memory. With the server's prompt cache disabled, earlier cases' prompts are not kept in RAM.

### Full runs

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
| Arithmetic | 13 | Six controls and seven multi-step challenges (successive percentages, precedence, work rate, overnight time, compound interest with withdrawals, clock-hand angle, mixture), in an enforced JSON `answer` object |
| Structured output | 13 | Six controls and seven challenges (nested arrays of objects, number words, escaped quotes, filter-and-sort, grouping, counting with a filter, 24-hour time conversion with sorting); typed values, exact fields, and array order |
| Agentic tools | 13 | Six controls and seven challenges; separate tool-use and exact-answer scores, plus strict end-to-end success |
| Vision | 13 | Six controls (presence, counting, large-text OCR) and seven challenges (counting among distractors, relative position, chart ranking, small-text reading with addition, table arithmetic, grid row comparison, containment) |
| Context | 13 | Five retrieval controls and eight challenges covering revisions, joins, filtering/counting, and absent evidence |

Arithmetic, vision, and context requests enforce a JSON object with exactly one key, `answer`, through Ollama's `format` schema. The value has the appropriate number, boolean, or string type, for example `{"answer":2.5}` or `{"answer":false}`. Prompts request the same object format, and the scorer extracts the value before checking correctness. Schemas contain no expected values or answer choices. Using an object avoids the answer-quality regression observed with Gemma E2B when enforcing bare-number output. The structured-output track uses prompt instructions without an enforced schema so it continues to test the model's ability to produce the requested structure. One surrounding Markdown code fence (```` ```json ... ``` ````) is accepted, since many models add one by habit; any other text around the JSON is still invalid. Responses are still scored for correctness; schema enforcement does not prevent wrong answers, token limits, or API failures. [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)

The arithmetic and agent tracks both include `173 × 29`. The agent case requires a calculator call. Tools are deterministic fixtures with state reset for each case. They do no I/O with hardware, files, or external services. Agents use native tool calls without a final-answer schema during tool selection. Once the model returns a response without tool calls, the runner includes that response in the conversation and requests one schema-constrained final `answer` object with tools removed. This avoids combining a final-answer grammar with native tool calling, which can suppress tool calls in Ollama. The extra request shares the case deadline and its tokens and latency are included in case metrics. Tool calls, arguments, order, and final state are still scored. [Ollama tool/format compatibility issue](https://github.com/ollama/ollama/issues/18944)

Agent results report three measures. **Tool-use success** requires the expected calls, arguments, and order (operands of `add` and `multiply` may appear in either order), plus the correct simulated state where applicable; the no-tool control requires a completed response without tool calls. **Exact-answer success** checks only the final JSON answer value. **End-to-end success** requires both. Correct tool use receives credit even when the final answer is a sentence instead of the requested short value. Invalid tool attempts and tool-budget violations fail tool-use scoring, and supported runtime failures stay in the denominator. Agent prompts explicitly request only the status field value or color/state word, without a sentence. No substring matching or model-based judging is used to accept verbose answers.

The agent challenges add intermediate calculator results, decisions based on order status, stopping after a missing-customer result, reading state before changing it, avoiding a redundant write, a six-call workflow involving two customers and final-state verification, and counting delivered orders across two lookups. A deterministic `read_indicator` fixture is available only in the state-reading challenges; controls retain their original four tools. Each case resets its simulated state, including an already-green case that requires no write. New prompts specify the required sequence so exact trace scoring does not reject alternative plans that the prompt left open. Native tool-call IDs, when supplied, are returned with their tool results.

Context challenges use both 16-record short logs and 96-record long logs. They ask for the highest approved revision despite later pending/cancelled records, resolve an alias through two cross-references, count records satisfying several exact conditions, and distinguish missing assignments from similar IDs and non-authoritative comments. Inputs and targets are fixed by case ID and suite version, identically for every model. The original 8- and 90-record retrieval cases remain as controls. Context requests disable automatic prompt truncation: an input that does not fit the selected `--context` fails explicitly rather than silently losing evidence.

Reports show `control` and `challenge` success counts separately when both are present. `cases.jsonl` stores the difficulty label and `summary.json` includes `by_difficulty` breakdowns. This distinguishes success on the original controls from performance on the new workloads; a harder design does not guarantee a particular failure rate.

Each capability-dependent case is marked `unsupported` when the capability is absent from Ollama metadata. API errors and timeouts on supported cases count against pass rate. Compare models using the same suite version and settings; [CHANGELOG.md](../CHANGELOG.md) lists what changed in each version. Existing result files retain their original scores; rerun into a new output directory to evaluate the new protocol. Reports of older agent records without separate checks display `tool-use checks unavailable` rather than inferring new scores from today's fixtures.

The object format was chosen after inspecting responses on these fixtures. Treat the existing results as exploratory benchmark development. For an independent assessment, freeze the protocol and evaluate additional cases that were not used to choose it.

## Phases

| Phase | What the runner does |
| --- | --- |
| Cold | Checks that Ollama has no resident model, then times the first text request and records model load time. The operating-system page cache is retained and uncontrolled, so this measures an unloaded-model request. |
| Warm | Loads and warms the model once and runs every warm case, reloading only after a failed warmup, an out-of-memory error or an eviction. On the Pi, the runner waits for the thermal band before loading; the cases then run at whatever temperature the workload reaches. |
| Sustained | Starts with a warmed model inside the same thermal band and runs sequential decode requests for at least 90 seconds by default (`--sustain-seconds`). The final request can finish after the window. The report shows actual duration and first-versus-last-third decode speed when at least three requests complete. The requests don't use the 65 cases; see [Sustained phase prompt](#sustained-phase-prompt). |

### Sustained phase prompt

Every sustained request sends the same prompt (`DECODE_PROMPT` in `light_eval.py`):

> Write a numbered list of at least sixty distinct short sentences about maintaining a garden. Begin with item 1 and continue the list as far as possible.

- **The prompt is open-ended on purpose.** Each request is capped at 128 generated tokens (`--sustain-tokens`), and a 60-item list never finishes within that cap. So every request generates the full 128 tokens, and the requests are directly comparable.
- **Only speed is measured.** The text is not scored. Decode speed is compared between the first and last thirds of the requests, to show whether continuous generation slows the device down, for example through heat.
- **Prefix reuse is allowed.** The model stays loaded and repeats the same prompt, so later requests can reuse the processed prompt from the live slot. The phase therefore measures generation speed, not prompt processing.
- **The thinking mode is the run's.** In the official thinking-off run, it's off.

## Fairness and repeatability

- One cycle gives each model every run position exactly once. With three models, that means three rounds and nine model slots. Cycles are paired: the second cycle reverses the cyclic ordering, so for each leading model the remaining models run in reverse order. With three models, `--cycles 2` guarantees all six possible model orders exactly once, for any seed; each additional pair of cycles repeats that guarantee. Models see the same case order within each round.
- The default thermal band is set from the idle temperature, from 4°C below to 3°C above baseline, with a 65°C ceiling. `--temp-min` and `--temp-max` let you set an explicit band. The runner checks Pi temperature and current `vcgencmd` flags at each gate. Gates come before the cold request and before each model load, so every model starts from the same temperature; there is no gate between warm cases. Under load the Pi warms beyond the band (to about 65–69°C with the cooler used so far). Temperature doesn't change answers, and only affects speed if the Pi throttles, which starts at about 80°C. The runner records temperature, memory, swap, and throttling throughout measured workloads. `summary.json` and `report` give, for each model and phase, the range and median of case start temperatures, the time-weighted mean, the peak, and the number of cases with throttling flags; treat throttled cases' timings as suspect. A gate waits up to `--cooldown` seconds (default 900). If the band still isn't reached, the run continues with a warning, and the miss is recorded in the manifest under `thermal_gate_timeouts`, so an unattended run isn't lost to a slow cool-down. [Raspberry Pi diagnostics](https://www.raspberrypi.com/documentation/computers/os.html#vcgencmd)
- Defaults use Ollama backend selection on Mac and CPU inference on the Pi, with two CPU threads, a 64-token prompt-processing batch, temperature zero, and seed 42. The smaller batch limits processing workspace without shortening the input or output budget. The runner uses native model templates and records their hashes and the installed model digests. Context and generation budgets depend on the thinking mode, unless set with `--context` and `--max-tokens`: thinking off uses a 4,096-token context and 1,024 tokens per model turn; thinking on uses 10,240 and 6,144, because `qwen3.5:9b` exhausted a 3,072-token budget on one arithmetic case. The generation budget covers both thinking and the final answer. A 600-second (10-minute) case budget covers all turns; override it with `--timeout SECONDS`. Agent budgets default to six tool-call turns and eight calls; lowering them can make the longer challenges impossible to complete. `--thinking off` (the default) and `--thinking on` request that mode from models that advertise thinking; `--thinking default` leaves each model's own default. Evaluate the two modes as separate runs and compare models only within one mode: with thinking off, `qwen3.5:4b` solved 5 of 12 arithmetic cases; with thinking on, 12 of 12, at 15-50 s per case instead of under 1 s.
- Each case prompt is sent exactly as written, with nothing prepended. Earlier versions prefixed a hash "case reference", which some models treated as task input: they used it as an order ID or customer name, or refused because they could not look it up. With the server's prompt cache disabled, cache reuse happens only between turns of the same case. The sustained phase keeps the model resident and repeats one prompt, allowing native prefix reuse. Cached token counts are logged when Ollama returns them. The context cases keep the underlying prompt identical across models and lengths are reported in each model's own tokens. Reducing `--context` can change whether the full longer prompt fits.
- Repeatability reports how many cases kept the same scored outcome and how many with complete responses produced identical streamed content, thinking, and tool calls across rounds. Temperature 0 and a fixed seed don't make outputs fully identical: in the official Pi run, 74–82% of cases repeated word for word across three passes, and the pass or fail result differed between passes for 2 of 260 model–case pairs. A likely cause is that the model stays loaded and the case order changes each round, so each case can start from different cached state. Scores are averaged over passes. The benchmark uses 65 deterministic cases, 13 per track (29 controls and 36 challenges), and eleven synthetic images. `fixtures/make_fixtures.py` regenerates the seven challenge images; it needs Pillow and is never imported by the runner. Use its results for the tested workloads.

## Reading the results

| File | Contents |
| --- | --- |
| `manifest.json` | Suite version, options, seed, schedule, case difficulty and offered tools, response schemas, agent final-answer policy, hardware and software identifiers, model digests, thermal band, source and image hashes, and run status |
| `cases.jsonl` | One row per case with outcome, difficulty, response schema, agent tool-use and exact-answer checks, wall time, time to first streamed output, time to first text chunk, per-turn format constraints, Ollama durations and token counts, response content, tool trace, and resource samples summarized over that case |
| `blocks.jsonl` | Per-model and per-phase runtime, residency information, and resource measurements |
| `telemetry.jsonl` | Timestamped temperature and throttling where available, Linux memory/OOM readings, Mac free/compressed/wired memory, and system-wide swap usage and I/O |
| `summary.json` | Track-level and difficulty-level success counts, separate agent tool-use and exact-answer totals and rates, latency and decode distributions, output repeatability, sustained decode comparisons, and per-run CPU temperature and throttling |

Time to first token in the summary means time to the first nonempty streamed text, thinking, or tool event. The benchmark also records first text separately. Decode tokens/s is calculated from Ollama's generation count and duration. Token units vary across models, so compare end-to-end time and task success alongside tokens/s. Timing distributions use completed responses, with outcome counts and timing sample sizes shown alongside them. p95 is reported from 20 or more measurements using the nearest rank. The system-used-memory estimate uses Linux `MemTotal - MemAvailable`; Mac free and compressed memory are reported separately rather than treated as equivalent to Linux available memory. All memory and swap readings include background processes. Swap I/O deltas are logged in native pages and MiB, using the measured page size. Existing swap allocation does not by itself establish active paging or identify which application caused it. [Ollama chat API](https://docs.ollama.com/api/chat)

## Tests

Run the automated checks with the command below. They use a mock Ollama server and can be run on the Pi or Mac without installed models.

```bash
python3 -m unittest discover -s tests -v
```

If pytest is installed, you can also run the tests from the project root:

```bash
pytest tests/test_benchmark.py
```
