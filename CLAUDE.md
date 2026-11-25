# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Light Eval benchmarks local Ollama models for a **Raspberry Pi 5 with 8 GB RAM**, the final target. The Mac (16 GB, Metal) is for fast exploratory runs. The runner uses only the Python standard library (3.10+): no packages, no API keys. It talks to an already-running local Ollama server over loopback HTTP and never starts or reconfigures the server.

## Commands

```bash
# Tests (mock Ollama server; no models needed; tests/test_publish.py covers the publishing step)
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

# Show one case's prompt, expected answer and every model's output from a published run
python3 tools/show_case.py docs/results/pi5-off-official json-24h-times

# Build and preview the results site (GitHub Pages; docs/SITE.md). site/data/ is generated and gitignored
python3 tools/build_site.py && python3 -m http.server -d site 8000

# Publish a run (copies it into docs/results/, which is committed) and rebuild the README results
python3 tools/publish.py add results/<name> --name <device>-<off|on>-<model>   # protocol 1-2 multi-model runs: --rounds 1-3
python3 tools/publish.py build
```

`--out` must be a new directory. `results/` is gitignored, so run outputs are never committed. Keep them in `results/` and not in a scratch or temp directory, or they will be lost. Runs worth publishing go into `docs/results/` with `tools/publish.py add`; that copies the manifest, cases and blocks, without the large telemetry file.

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
  - `rounds()` builds the seeded model order for `--passes N` (default 3): every model runs once per pass, whatever the number of models. The orders are a prefix of balanced cycles (each block of len(models) passes puts every model in every position once). `--passes` replaced `--cycles` in protocol 3; the runner rejects `--cycles` (see `docs/OFFICIAL_RUNS.md`).
  - `run_locked()` writes the manifest and opens the output files.
  - `run_slot()` runs the cold, warm and sustained phases for one model.
  - `run_case()` handles one case, including the agent loop: native tool calls first, then one schema-constrained final answer with tools removed, all within one deadline.
  - `summarize()` produces `summary.json`.
  - `apply_budget_defaults()` sets context/max-tokens by thinking mode: 4096/1024 with thinking off, 10240/6144 with thinking on.

`tools/publish.py` imports `summarize` and `temperature_summary` from `light_eval.py`. `build` keeps only published runs of the current `VERSION`, and rewrites the README between `<!-- results:start -->` and `<!-- results:end -->` plus the light/dark SVG charts in `docs/assets/`. Never hand-edit that part of the README. The generated section leads with an at-a-glance table and three charts (`tools/charts.py`: thinking dumbbell, per-track heatmap, thread comparison), with all detailed tables inside a collapsed `<details>` block. The accuracy-and-speed bubble chart (`svg_chart`) keeps the full 920 px width for the plot (legend in a row above it), fits both axes to the data, and places labels by scoring 16 directions per bubble and then repeatedly re-placing each label until none improves. `BubbleChartTest` enforces its rules on the published chart and a crowded synthetic one: every label inside the plot, clear of other labels and bubbles, and nearest its own bubble. After adding models, render the chart and look at it (headless Chrome renders the SVGs at full width; `qlmanage` crops them to a square). The bottom line above the markers and the findings below them are hand-written: update them when results change. The main tables and charts use `PRIMARY_THREADS` (4, the runner's default). Official runs at other thread counts appear in the thread comparison, except a model and mode measured only at another thread count (gemma4:e4b), which appears in the main results marked "2 threads only". `docs/ANALYSIS.md` is hand-written from `docs/results/pi5-off-official`, round 1: if that run is replaced or the suite version changes, update the analysis too. CI (`.github/workflows/tests.yml`) runs the tests and fails if `build` changes `README.md` or `docs/assets/`, so run `build` after publishing or after changing `VERSION`.

The results site (`site/`, deployed by `.github/workflows/pages.yml`) reads `site/data/`, which `tools/build_site.py` generates from the same `entries()` and `primary_group()` as the README, so its numbers always match. `site/index.html`, `style.css`, `app.js` and `meta.json` are hand-written; the overview intro and About text live in `index.html`. After changing the site, render its pages at desktop and 390 px widths (a 390 px iframe gives a true phone viewport; headless Chrome windows have a larger minimum width) and look at them.

Each run writes five files: `manifest.json` (options, hashes, model digests, server settings, swap devices), `cases.jsonl`, `blocks.jsonl`, `telemetry.jsonl` and `summary.json`.

## Rules that span files

- **Any change to cases or scoring bumps `cases.VERSION`.** Also add an entry to `CHANGELOG.md`, update the suite badge in `README.md` and `version` in `CITATION.cff`, and update the tests that pin the suite version and the per-track and per-difficulty counts (`test_expanded_suite_preserves_controls_and_fits_tool_budgets`, `test_cli_mock_ollama`). Compare results only within one suite version and one thinking mode.
- **A change to how runs are made that could alter timings or failure classification, without changing any case, bumps `light_eval.PROTOCOL_VERSION`** instead, with a `CHANGELOG.md` entry. A new suite version would retire every published result, since `build` shows only the current one.
- **`tools/publish.py add` refuses runs that don't match the published protocol** (`run_problems`): only some tracks, non-standard budgets, the prompt cache not disabled, changed cases or fixtures, non-standard phases for an official run, no warm cases, incomplete rounds, server/connection errors (including a failed sustained warmup), a thinking-on model that produced thinking text in fewer than half its answered cases, or a thinking-off model that produced it in more than 2. `add` also publishes only allowlisted server settings (`runtime.recordable_setting`). Don't work around a refusal; fix the run or ask. Its tests are in `tests/test_publish.py`.
- **Prompts must not leak answers.** `test_requests_do_not_depend_on_expected_answers_or_scoring_rules` enforces this. New or changed cases are checked by a fresh subagent that sees only the exported prompts and images, never the repo; it must answer 100% correctly before the case is committed. Agent cases are excluded from that check, because solving them needs the tool simulator in the source.
- **When adding a vision case**, add a drawing function to `fixtures/make_fixtures.py` (suite 1.8 drawings: `fixtures/suite_1_8_fixtures.py`), commit the PNG, and check that the existing images still regenerate byte for byte. Photos (suite 1.8) go through `fixtures/prepare_photos.py`, which strips all metadata (the originals carry GPS), blurs bystanders' faces and scales them; never commit an original.
- **Suite 1.8 is being written in `suite_1_8.py`** (images in `fixtures/v1_8/`, tests in `tests/test_suite_1_8.py`; the code track's cases are in `suite_1_8_code.py` (each with a reference, wrong solutions or mutants, and expected values computed from the reference; checked by `tests/test_suite_1_8_code.py`), and its test runner is `code_runner.py`, tested in `tests/test_code_runner.py`. It runs model code only inside an OS jail (bubblewrap on Linux, so the Pi and CI need the `bubblewrap` package; a deny-by-default sandbox-exec profile on macOS) and refuses to run without one; scoring happens in the parent, which keeps the expected values; test-generation answers are data, so no model code runs for them; the Python-level checks are defence in depth, not the boundary. Any change to a safeguard needs a test that fails without it), outside `cases.dataset()`, so suite 1.7 and its published results stay unchanged until the switch. The plan is `docs/SUITE_1_8_DESIGN.md`.
- **The warm phase loads each model once** and reloads only after a failed warmup, an out-of-memory error, a server or connection error, or an eviction. This depends on the Ollama server running with `LLAMA_ARG_CACHE_RAM=0`; with the default 8 GiB prompt cache, memory grows with every case. The runner records the value as `server_prompt_cache_ram_mib` and warns when it isn't 0. Setup is in `README.md` (Quick start) and `docs/METHODOLOGY.md` (Prompt cache).
- **The runner refuses to start if any model is resident.** Unload models (`ollama stop MODEL`) and keep other Ollama clients idle, including `ollama launch` sessions.

## Models and platforms

- **Mac and Pi must run identical model builds.** Compare the IDs in `ollama list`. On the Mac use `gemma4:e2b-it-q4_K_M` and `gemma4:e4b-it-q4_K_M`; the plain tags can resolve to different builds on Apple hardware, for example an nvfp4 MLX build.
- **`LFM2.5-1.2B-Instruct` and `lfm3-omni:2b` have no thinking-on results.** `lfm3-omni` has no thinking capability. LFM2.5 Instruct has the flag, which Ollama derives from its GGUF chat template, but it never reasons: with `think: true` its thinking text is empty.
- **qwen3.5:9b is excluded** because it runs at about 1.8 tokens/s on the Pi.
- **gemma4:e4b doesn't reliably fit on the Pi.** In the official thinking-off run it filled RAM and zram in every pass, and in its fourth pass the Pi hung and rebooted. It is excluded from thinking-on runs.
- **Outputs are close but not identical between passes** (on the Pi, 74–82% identical with thinking off, 18–77% with thinking on, where up to 6 of 65 outcomes change per model). Never claim answers are deterministic; scores are averaged over passes.
- **Raspberry Pi OS swaps to zram**, which is compressed RAM. A full swap there means RAM is exhausted.
