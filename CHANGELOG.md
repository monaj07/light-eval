# Changelog

Each run records the suite version in `manifest.json`. Scores are comparable only between runs with the same suite version and the same thinking mode.

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
