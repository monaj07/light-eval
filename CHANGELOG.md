# Changelog

Each run records the suite version in `manifest.json`. Scores are comparable only between runs with the same suite version and the same thinking mode.

Since protocol 2, runs also record `protocol_version`: how runs are made, separately from the suite version, which covers cases and scoring. A protocol change can alter timings or how failures are classified without changing any case, so it no longer needs a new suite version, which would retire every published result.

## Suite 1.8.0 and protocol 4 (2026-11-26)

Suite 1.8.0 replaces suite 1.7.0 as the runner's default: 160 cases in eight tracks of 20 (arithmetic, structured, agent, vision, context, and the new RAG, instructions and code tracks), each with control, challenge and hard tiers. The cases, and why each 1.7 case was kept, reworked or retired, are in [`docs/SUITE_1_8_DESIGN.md`](docs/SUITE_1_8_DESIGN.md). Suite 1.8 runs record protocol 4.

- **Only suite 1.8 runs can be published.** `tools/publish.py add` refuses suite 1.7 runs, runs from the pilot (`1.8.0-pilot`, made before the calibration below), runs with another protocol, and runs whose suite 1.8 case files (`suite_1_8*.py`, `code_runner.py`, `cases.py`, the images in `fixtures/v1_8/`) differ from today's.
- **Suite 1.7 can still be run** with `--suite 1.7`, which takes protocol 3's path unchanged and records protocol 3, so its published results stay reproducible.
- **The README and the site keep showing suite 1.7 until suite 1.8 has an official Pi run published**, then switch to it on their own; a Mac or exploratory suite 1.8 run doesn't trigger the switch. A note above the README results and a banner on the site say so meanwhile. Once switched, a later suite 1.8 version (1.8.1) retires the 1.8.0 results as usual but never brings suite 1.7 back. `build` deletes chart SVGs it no longer draws, such as the thread comparison, which suite 1.8 drops.
- **Official suite 1.8 runs make one pass** (`--passes 1`); `add` refuses more. The 95% interval counts every attempt as a trial.
- **The code sandbox lets the standard library finish its own imports.** `datetime`'s `strptime`, `strftime` and `date.today()` import `_strptime` or `time` from C, and the sandbox's import guard refused those, so correct date code failed with an ImportError. The Pi rehearsal found it: a correct `code-business-days` answer was scored `code_error`. The guard now lets exactly those two modules through to the C code that asks for them, and the model's own `import time` or `import _strptime` is still refused. This came before any suite 1.8 run was published, so the suite stays 1.8.0; runs made before the fix can't be published, since `code_runner.py`'s hash differs.
- **Planted-instruction cases whose warmup failed** (out of memory, say) are flagged `injection` too, so `summary.json` and the README count the same cases, as not followed. After the switch, the site keeps suite 1.7's results as an archive view (`?suite=1.7`, built into `site/data/archive/1.7/`), labelled as the previous suite.
- **Results for suite 1.8 show:** a hard-tier column; a 95% Wilson interval for each accuracy, which also sets the ties in the site's ranking (suite 1.7 keeps its ranges across passes); how many planted instructions each model followed; and how many cases of the repeatability sample changed outcome. The heatmaps gain the three new tracks.

The rest of this entry describes protocol 4, which was first written for pilot runs of the staged cases:

- **One pass plus a repeatability sample.** By default, every model runs the cases once (`--passes 1`). Then, with the model still loaded, it repeats a fixed 24-case sample, one case per tier and track (three hard ones for code), chosen by a fixed seed so every run repeats the same cases. Scores come from the first attempt only. The repeated rows have the phase `repeat`, and `summary.json` reports how many of them changed outcome.
- **New case types:**
  - instructions: free text checked by rules, with no JSON format;
  - RAG: answer and sources, with `null` allowed in every case's format;
  - code: model code is tested in the OS jail, and test generation is data;
  - agent: suite 1.8's own simulator.

  Each case type scores itself. Records add `failed_checks`, `rag_evaluation` and `scoring_s`, and planted-instruction cases are flagged `injection`.
- **Agent tool budget:** 12 rounds and 16 calls by default (suite 1.7: 6 and 8), since hard cases need up to 8 calls plus retries. Malformed tool calls return an error result to the model, as real tools do, instead of ending the case.
- **`wall_s` stops at the final answer.** Running a code answer's tests is recorded separately as `scoring_s`, and the case's temperatures and memory are taken at the answer too, so a slow test run doesn't count against it. `scoring_s` has no deadline of its own: each jailed test process has its own limit, so a test-generation case, which runs the reference and every mutant, can take a minute or more.
- **Tool errors instead of crashes.** A tool result that isn't finite (a division by 1e-300, say) becomes an error result for the model. Before this fix, writing such a result to `cases.jsonl` would have aborted the run.
- **Planted instructions are counted from what the model did.** Each row records `injection_followed` from the tool trace or the reply, whatever the final status, so a model that obeys the instruction and then times out or runs out of tool calls is still counted.
- **Suite 1.7 records keep protocol 3's shape:** the new fields (`scoring_s`, `injection`, `options.suite`) are written only for pilot runs.
- **`--plan` needs no sandbox,** and the manifest hashes only the staged images (`.png` and `.jpg`), never stray files such as `.DS_Store`.
- **The code track needs an OS sandbox.** The runner checks for one before starting and refuses to start without it (on Linux, `sudo apt install bubblewrap`).
- **`summary.json` adds** a per-model `injection` count (planted-instruction cases, followed and passed) and `rag_metrics` (answers and sources) for the RAG track. The printed summary shows the hard tier.
- `manifest.json` hashes the staged images (`fixtures/v1_8/`) and records whether each case has a planted instruction.
- **Calibration from the first Mac pilot** (`lfm3-vl:2b`, both thinking modes):
  - agent prompts with a string answer now say the value must be only the requested value, not a sentence (suite 1.7's agent prompts said this; the pilot model gave correct answers wrapped in sentences, such as "Ticket T-91 is closed.");
  - `instr-inner-tube-json`, the one hard case every pilot run passed, moves to challenge, and `instr-shopping-list`, a challenge no run passed, moves to hard, so the track keeps 3/7/10.

## Protocol 3 (2026-11-23)

Cases and scoring are unchanged (suite 1.7.0).

- **`--passes N` replaces `--cycles`.** Every model runs N times (default 3), whatever the number of models. With `--cycles`, one cycle meant one pass per model in the run, so the pass count depended on how many models were listed (3 for three models, 4 for four). The runner rejects `--cycles` with a message pointing to `--passes`.
- The model orders are the first N of the same balanced, seeded schedule, so three passes of four models run in exactly the order of the first three rounds of a protocol 1–2 run, which is what was published for those runs.
- Single-model thinking-on runs use the default of 3 passes, or `--passes 1` for the 2-thread comparison.
- **Fixes from a second review of protocols 2 and 3.**
  - An input longer than `--context` is `context_overflow`, the model's result like out of memory, not `api_error`, which counted as a server fault and made `add` refuse the run.
  - A failed sustained-phase warmup, which writes no case row, counts as an infrastructure error, so the run ends `complete_with_errors`.
  - `server_environment` records only an allowlist of performance settings (flash attention, KV-cache type, parallelism and so on, plus `LLAMA_ARG_*`), never anything named like a credential. Protocol 2 recorded every `OLLAMA_*` variable, which could include a key, host or path; `add` applies the same allowlist to manifests made before this fix.
  - A zram device whose statistics can't be read leaves `zram_ram_kib` out of that sample, so peak memory is reported as unknown rather than overstated.
  - The thermal band also narrows when the idle baseline is within 3 °C of `--temp-max` (since protocol 2), not only when the first gate times out.
- **Publishing (`tools/publish.py add`) also refuses:** an official run without its mode's phases (thinking off: cold, warm and sustained; thinking on: warm), a run or model with no warm cases, non-standard `--sustain-seconds`, `--sustain-tokens` or `--seed`, and a thinking-off model with thinking text in more than 2 cases. The thinking checks count only cases with model turns, so failed warmups aren't misread as a missing thinking mode.

All published results are unchanged: `build` produces the same README and charts.

## Protocol 2 (2026-11-22)

Cases and scoring are unchanged (suite 1.7.0). Changes from an independent review of the runner and the publishing step:

- **Runner.**
  - `manifest.json` records `protocol_version` and `server_environment`, every `OLLAMA_*` and `LLAMA_ARG_*` setting of the Ollama server (flash attention, KV-cache type, parallelism and so on), not only `LLAMA_ARG_CACHE_RAM`.
  - Prompts are never silently truncated, on every track, not only context: an input that doesn't fit `--context` fails explicitly.
  - After a server or connection error, not only out of memory, the model is reloaded and warmed up before the next case, so a crashed runner's reload isn't timed as part of a case.
  - A model's malformed tool call that Ollama reports as a server error (such as "XML syntax error") is `invalid_tool_call`, the model's failure, not `api_error`.
  - A run with server or connection errors ends as `complete_with_errors`, with counts under `infrastructure_errors`, instead of `complete`.
  - If the first temperature gate times out hot, the thermal band is the 7 °C below `--temp-max`, not an empty range that every later gate would wait out in full.
  - On Linux, every sample records `zram_ram_kib`, 0 without zram, so swap on disk counts in full. A peak that can't be computed for every sample is reported as unknown, not as the peak of the others.
  - Temperature means across rounds are weighted by time (telemetry samples), as documented, not by cases.
  - A truncated last line in `cases.jsonl` or `blocks.jsonl`, left by a crash or hang mid-write, is skipped with a warning, and a failure writing `summary.json` no longer hides the run's own error.
- **Publishing (`tools/publish.py`).**
  - `add` refuses a run whose results would mean something different from the published ones: only some tracks, non-standard budgets for its thinking mode, the prompt cache not disabled, changed `cases.py` or fixtures, an incomplete round, or server or connection errors (`--allow-errors` with a `--note` accepts these). It checks everything, including `--flag`, before creating any files.
  - Supported tracks come from the model's recorded capabilities, not from which tracks have results.
  - `build` fails on two official runs of the same model, thinking mode and thread count, and leaves out, with a warning, a model with no timed cases.
  - Labels follow the data: the case time limit, the thread counts compared, and challenge totals for models without every track. The speed table shows only thinking-off runs, as its title says.
- **Tests and CI.** New tests in `tests/test_publish.py` cover the publishing step and the cases above. CI also fails when `build` leaves new, uncommitted chart files.

All published results are unchanged: `build` produces the same README and charts.

## 1.7.0 (2026-11-18)

- Replaced two arithmetic controls that every model passed (`math-decimal`, `math-fraction`) with two challenges: clock-hand angle and mixture. Arithmetic now has six controls and seven challenges, like the structured, agent and vision tracks.
- The suite has 65 cases: 29 controls and 36 challenges.
- Added after release: `--threads` defaults to 4, all of a Pi 5's cores, instead of 2. The README results now use the 4-thread runs, with the 2-thread runs kept as the comparison. Cases and scoring are unchanged; with thinking off, accuracy was the same at both thread counts.
- Added after release, as reporting only (cases and per-case scoring unchanged): accuracy is reported over the tracks a model supports. A track it can't attempt, such as vision for a text-only model, shows as "n/a" and is no longer counted as 0%. The README shows how many of the 5 tracks each model covers, and the full results add accuracy over the 4 text tracks for strict like-for-like comparison. The runner refuses `--thinking on` for a model without a thinking mode, instead of quietly running it with thinking off. Results for models that support every track are unchanged.
- Added after release: a temperature gate that times out no longer stops the run. The run continues, records the miss in `manifest.json` under `thermal_gate_timeouts`, and `--cooldown` defaults to 900 s instead of 180 s. Runs whose gates were all met behave exactly as before.
- Added after release, as telemetry only: on Linux, each sample records the RAM zram uses (`zram_ram_kib`, from `/sys/block/zram*/mm_stat`). Each phase reports `peak_memory_in_use_kib`, which is RAM in use plus swapped pages, minus zram's own RAM, computed sample by sample, so zram pages are counted once.
- Added after release, as reporting only (scores and protocol unchanged): `summary.json` and `report` give each Pi run's CPU temperature (case start range, mean, peak) and the number of cases with throttling flags.

## 1.6.0 (2026-11-18)

- Brought every track to 13 cases by adding seven challenges:
  - one arithmetic: compound interest with withdrawals;
  - three structured: grouping, counting with a filter, and 12-hour to 24-hour conversion with sorting;
  - three vision: table arithmetic, grid comparison and containment.
- `fixtures/make_fixtures.py` draws the three new images and still reproduces the earlier ones byte for byte.

## 1.5.1 (2026-11-18)

- One model load for the whole warm phase. Reloads happen only after a failed warmup, an out-of-memory error or an eviction, and `--reload-every` is removed.
- The Ollama server must run with `LLAMA_ARG_CACHE_RAM=0`. The manifest records the setting as `server_prompt_cache_ram_mib`, and the runner warns when it isn't 0.
- The manifest records `/proc/swaps` as `swap_devices`.
- Cases and scoring are the same as 1.5.0.

## 1.5.0 (2026-11-17)

- Removed the hash "case reference" that had been prepended to every prompt. Some models treated it as task input.
- Structured output may be wrapped in one Markdown code fence, and calculator calls may give `add` and `multiply` operands in either order.
- Added 13 challenge cases (58 in total).
- Added `--thinking on` (2026-11-18), with larger default budgets: a 10,240-token context and 6,144 tokens per turn.

## 1.4.6 (2026-11-17)

- Reloaded the model every 10 warm cases to limit Ollama's prompt cache. Replaced in 1.5.1.

## 1.4 to 1.4.5 (2026-11-17)

- 1.4 added agent and context challenges and difficulty reporting, and raised the default tool budgets for the longer plans.
- 1.4.3 used the existing Ollama server, reset the model between warm cases, allowed native Mac acceleration and used a smaller processing batch. Mac memory telemetry became passive.
- 1.4.4 kept each model resident through its warm suite.
- 1.4.5 recovered after an out-of-memory crash even when Ollama still listed the crashed runner.

## 1.0 to 1.3 (2026-11-16)

- 1.1 enforced scalar answers.
- 1.2 replaced them with `answer` objects and matching prompts and scoring.
- 1.3 added separate agent tool-use and exact-answer metrics, and clarified the answer instructions.
