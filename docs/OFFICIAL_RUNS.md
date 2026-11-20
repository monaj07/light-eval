# Official runs

This is the protocol behind the published Raspberry Pi 5 results. It uses suite 1.7.0 and these models: `qwen3.5:2b`, `qwen3.5:4b`, `gemma4:e2b` and `gemma4:e4b`.

## Plan

| | Thinking off | Thinking on |
|---|---|---|
| Invocation | one run with all four models, `--cycles 1` | one run per model |
| What runs | 4 rounds; each model takes every position once and answers the 65 cases 4 times | one pass of the 65 cases |
| Phases | cold, warm, sustained | warm only |
| Expected time on the Pi | about 10 hours | from about 4.5 hours (`qwen3.5:2b`) upwards per model |

**Why not `--cycles 2`.** With four models, `--cycles 2` would mean 8 rounds. With thinking off, 4 rounds already give a balanced order, a timing spread and a repeatability check. Outputs are close but not identical between passes: in the official run, 74–82% of cases repeated word for word, and the pass or fail result differed between passes for 2 of 260 model–case pairs. So the scores are averaged over the passes.

**Why thinking-on runs are warm only.** Cold and sustained measure the hardware: model load time and whether speed holds under continuous generation. They don't depend on the thinking mode, so they're measured once, with thinking off.

**`gemma4:e4b` is not run with thinking on.** In the official thinking-off run, it filled RAM and zram in every pass. In its fourth pass the Pi hung and rebooted, which also ended the run. Thinking on uses a 2.5× larger context, so it would almost certainly hang the Pi again.

## Setup

Every official run uses the same temperature band, 30–50 °C:
- Before each model loads (and before the sustained phase), the runner waits up to 15 minutes (`--cooldown`, default 900 s) for the CPU to cool into the band.
- Without an explicit band, each run would derive one from its own starting temperature. A thinking-on run that starts straight after another would then measure a hot Pi as "idle".
- If a gate runs out of time, the run continues. The miss is recorded in `manifest.json` under `thermal_gate_timeouts`.

Every official run passes the same `--setup` text, so the hardware is recorded in each `manifest.json`:

```bash
SETUP="Raspberry Pi 5 8 GB; Raspberry Pi OS Lite 64-bit, headless; official Raspberry Pi Active Cooler; clear vented acrylic case with lid; official Raspberry Pi 27 W USB-C power supply; SanDisk Extreme 128 GB microSDXC UHS-I; Ollama systemd service with LLAMA_ARG_CACHE_RAM=0; 2 GB zram; room about 22 C (estimated)"
```

## Before each run (on the Pi)

1. **Repo at the current commit.**
   ```bash
   cd ~/light-eval && git fetch origin && git status -sb
   ```
   It must report `## main...origin/main` with no local changes, and `git log -1` must match GitHub.

2. **Same Ollama version for the whole campaign.** Check it with `ollama --version` and don't upgrade Ollama between official runs.

3. **Prompt cache disabled.** `systemctl show ollama -p Environment` must include `LLAMA_ARG_CACHE_RAM=0`.

4. **Models present.** `ollama list` must show the four models, with these IDs:

   | Model | ID |
   |---|---|
   | `qwen3.5:2b` | `324d162be6ca` |
   | `qwen3.5:4b` | `2a654d98e6fb` |
   | `gemma4:e2b` | `b37049369adf` |
   | `gemma4:e4b` | `dc35e8d9c606` |

5. **Clean state.**
   - Reboot, and don't use the Pi for anything else during the run.
   - After 5 minutes idle, `vcgencmd measure_temp` should read below about 47 °C, comfortably inside the band. If it's higher, the room or the cooling differs from the recorded setup: fix that, or raise `--temp-max` and note it in `$SETUP`.
   - `ollama ps` lists nothing.
   - `vcgencmd get_throttled` prints `throttled=0x0`.

6. **Run inside tmux on the Pi**, so a dropped SSH connection doesn't stop the run.
   - A tmux window on the Mac isn't enough: the benchmark would still run inside the SSH connection.
   - Install tmux if needed: `sudo apt install tmux`.
   - Start a session with `tmux new -s bench`. Detach with `Ctrl-b d`, and reattach later with `tmux attach -t bench`.

## Commands

Run these from `~/light-eval` inside the tmux session, after setting `SETUP` in that same shell.

**Rehearsal first (about 10–15 minutes).** It runs every phase and the gates between two models on the arithmetic track only, to check the whole chain before committing a night to it:

```bash
python3 light_eval.py run --models qwen3.5:2b gemma4:e2b --tracks arithmetic --sustain-seconds 20 \
  --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/rehearsal_v1_7
```

It should end with a summary for both models, `manifest.json` should show `"status": "complete"`, and `thermal_gate_timeouts` should be empty, or show how long cooling takes. Don't publish it.

Thinking off: all four models, all phases, 4 rounds.

```bash
python3 light_eval.py run --models qwen3.5:2b qwen3.5:4b gemma4:e2b gemma4:e4b \
  --cycles 1 --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/official_v1_7_off
```

Thinking on: one model per run, warm phase only.

```bash
for model in qwen3.5:2b gemma4:e2b qwen3.5:4b; do
  python3 light_eval.py run --models "$model" --phases warm --thinking on \
    --temp-min 30 --temp-max 50 --setup "$SETUP" --out "results/official_v1_7_on_${model//[:.]/_}"
done
```

A run that stops early leaves a partial folder, and `--out` must name a new folder. To retry, give the retry a new name and keep the partial run for reference.

## Thread count

The official runs use `--threads 2`: Ollama's runner is started with `-t 2`, which was confirmed on the Pi with `ps`. So the published runs use 2 of the Pi 5's 4 cores. Ollama on its own normally uses all physical cores, so these timings are probably slower than with Ollama's defaults. The thread count mainly affects prompt processing, which is limited by computation. Generation, which is limited mostly by memory bandwidth, should change less. Accuracy should stay within the normal variation between passes.

### Planned: thinking off with 4 threads

To be run after the thinking-on runs, with the same preflight checks and a reboot first:

```bash
python3 light_eval.py run --models qwen3.5:2b qwen3.5:4b gemma4:e2b \
  --cycles 1 --threads 4 --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/official_v1_7_off_threads4
```

- **Design:** 3 rounds, giving each model 3 passes like the published thinking-off run, with all phases. The only difference is `--threads 4`. `gemma4:e4b` is left out, because it hung the Pi with thinking off. Expect about 3–4 hours.
- **Check:** once the first model has loaded, `ps -o args= -C llama-server` should include `-t 4`.
- **Compare per model, at 2 and 4 threads:** prompt processing speed and median time per case; generation, in cases and sustained; accuracy and repeatability; CPU temperature and throttling; total run time and gate waits.
- **Decision:** if 4 threads is clearly faster, with the same accuracy and no throttling, later official runs should use 4 threads. The comparison will be shown separately from the main results, so tables never mix thread counts.

## What happened in the official thinking-off run

The Pi hung and rebooted in round 4, after 49 of `gemma4:e4b`'s 65 cases, because memory ran out. Rounds 1–3 had completed for all four models, so those were published with `--rounds 1-3`. The published manifest records the reason, and the run's own `"status": "running"` is kept as it was. See [the results](../README.md#results).

## Publishing

Copy each finished run folder to the Mac's `results/`, then:

```bash
python3 tools/publish.py add results/official_v1_7_off --name pi5-off-official
python3 tools/publish.py build
```

If a run stops partway, publish only its complete rounds with `--rounds 1-3 --note "why"`. `--flag "MODEL=text"` shows a warning in that model's memory column.

A run counts as official when it records a `--setup`. In the README, its rows replace the exploratory rows (marked †) for the same model and thinking mode.
