# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Light Eval benchmarks local Ollama models for a **Raspberry Pi 5 with 8 GB RAM**, the final target. The Mac (16 GB, Metal) is for fast exploratory runs. The runner uses only the Python standard library (3.10+): no packages, no API keys. It talks to an already-running local Ollama server over loopback HTTP and never starts or reconfigures the server.

## Commands

```bash
# Tests (mock Ollama server; no models needed)
python3 -m unittest discover -s tests -v
python3 -m pytest -q                                   # pytest.ini sets pythonpath
python3 -m pytest -q -k test_cli_mock_ollama           # a single test

# Print the schedule without connecting to Ollama
python3 light_eval.py run --models qwen3.5:2b qwen3.5:4b --plan

# Typical Mac exploratory run (warm phase only, no thermal gate)
python3 light_eval.py run --models qwen3.5:2b --phases warm --no-thermal-gate \
  --thinking off --out results/<new-name>

# Summarize an existing run
python3 light_eval.py report results/<name>

# Regenerate the challenge images (needs Pillow and macOS fonts; the runner never imports it)
python3 fixtures/make_fixtures.py
```

`--out` must be a new directory. `results/` is gitignored, so run outputs are never committed. Keep them in `results/` and not in a scratch or temp directory, or they will be lost.

## Architecture

There are three modules; each depends only on the ones listed before it.

- **`cases.py`**: the versioned suite and all scoring.
  - `dataset()` returns the 65 `Case`s: 13 in each of the arithmetic, structured, agent, vision and context tracks, each case marked `control` or `challenge`.
  - Context logs are generated deterministically (`context_case`, `challenge_context_case`).
  - The agent tools are a deterministic simulator: `TOOLS` and `dispatch()`, with state reset per case.
  - Scoring: `score()` returns a status (`pass`, `wrong_answer`, `invalid_json`, `wrong_tool_trace`, …). `equal_value()` uses exact typed comparison with an optional `tolerance`, and structured cases require integers for integer answers.
  - `Case.response_schema` derives the Ollama `format` schema from the answer's *type only*, never its value.
  - `VERSION` is the suite version recorded in every manifest.
- **`runtime.py`**: the `Ollama` client (streaming chat, `/api/ps`, unload), error classification (`api_error` maps OOM text to `out_of_memory`), passive telemetry (`sample()`, the `Telemetry` thread, `range_stats`, the thermal `gate`), and `server_setting()`, which reads an environment variable from the running Ollama server process (Mac: `ps -E`; Linux: `/proc/<pid>/environ` or `systemctl show ollama`).
- **`light_eval.py`**: the CLI and orchestration.
  - `rounds()` builds the balanced, seeded model order. Paired cycles reverse the order, so `--cycles 2` covers every order.
  - `run_locked()` writes the manifest and opens the output files.
  - `run_slot()` runs the cold, warm and sustained phases for one model.
  - `run_case()` handles one case, including the agent loop: native tool calls first, then one schema-constrained final answer with tools removed, all within one deadline.
  - `summarize()` produces `summary.json`.
  - `apply_budget_defaults()` sets context/max-tokens by thinking mode: 4096/1024 with thinking off, 10240/6144 with thinking on.

Each run writes five files: `manifest.json` (options, hashes, model digests, server settings, swap devices), `cases.jsonl`, `blocks.jsonl`, `telemetry.jsonl` and `summary.json`.

## Rules that span files

- **Any change to cases, scoring or the run protocol bumps `cases.VERSION`.** Also add an entry to the version history paragraph in `README.md`, and update the tests that pin the suite version and the per-track and per-difficulty counts (`test_expanded_suite_preserves_controls_and_fits_tool_budgets`, `test_cli_mock_ollama`). Compare results only within one suite version and one thinking mode.
- **Prompts must not leak answers.** `test_requests_do_not_depend_on_expected_answers_or_scoring_rules` enforces this. New or changed cases are checked by a fresh subagent that sees only the exported prompts and images, never the repo; it must answer 100% correctly before the case is committed. Agent cases are excluded from that check, because solving them needs the tool simulator in the source.
- **When adding a vision case**, add a drawing function to `fixtures/make_fixtures.py`, commit the PNG, and check that the existing images still regenerate byte for byte.
- **The warm phase loads each model once** and reloads only after a failed warmup, an out-of-memory error or an eviction. This depends on the Ollama server running with `LLAMA_ARG_CACHE_RAM=0`; with the default 8 GiB prompt cache, memory grows with every case. The runner records the value as `server_prompt_cache_ram_mib` and warns when it isn't 0. Setup is in `README.md`, under Prompt cache.
- **The runner refuses to start if any model is resident.** Unload models (`ollama stop MODEL`) and keep other Ollama clients idle, including `ollama launch` sessions.

## Models and platforms

- **Mac and Pi must run identical model builds.** Compare the IDs in `ollama list`. On the Mac use `gemma4:e2b-it-q4_K_M` and `gemma4:e4b-it-q4_K_M`; the plain tags can resolve to different builds on Apple hardware, for example an nvfp4 MLX build.
- **qwen3.5:9b is excluded** because it runs at about 1.8 tokens/s on the Pi.
- **gemma4:e4b barely fits on the Pi.** With thinking off it completes the suite but fills RAM and zram completely. Thinking on hasn't been tested on the Pi yet.
- **Raspberry Pi OS swaps to zram**, which is compressed RAM. A full swap there means RAM is exhausted.
