# Official runs

This is the protocol behind the published Raspberry Pi 5 results. It uses suite 1.7.0 and these models: `qwen3.5:2b`, `qwen3.5:4b`, `gemma4:e2b` and `gemma4:e4b`.

## Plan

| | Thinking off | Thinking on |
|---|---|---|
| Invocation | one run with all four models, `--cycles 1` | one run per model |
| What runs | 4 rounds; each model takes every position once and answers the 65 cases 4 times | one pass of the 65 cases |
| Phases | cold, warm, sustained | warm only |
| Expected time on the Pi | about 10 hours | from about 4.5 hours (`qwen3.5:2b`) upwards per model |

**Why not `--cycles 2`.** With four models, `--cycles 2` would mean 8 rounds. Answers are deterministic (temperature 0, fixed seed), so the extra rounds would add little beyond more timing samples. With thinking off, 4 rounds still give a balanced order, a timing spread and a check that outputs repeat.

**Why thinking-on runs are warm only.** Cold and sustained measure the hardware: model load time and whether speed holds under continuous generation. They don't depend on the thinking mode, so they're measured once, with thinking off.

**If `gemma4:e4b` runs out of memory with thinking on**, the run records it, and that is the result: it doesn't fit with thinking on.

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
for model in qwen3.5:2b gemma4:e2b qwen3.5:4b gemma4:e4b; do
  python3 light_eval.py run --models "$model" --phases warm --thinking on \
    --temp-min 30 --temp-max 50 --setup "$SETUP" --out "results/official_v1_7_on_${model//[:.]/_}"
done
```

A run that stops early leaves a partial folder, and `--out` must name a new folder. To retry, give the retry a new name and keep the partial run for reference.

## Publishing

Copy each finished run folder to the Mac's `results/`, then:

```bash
python3 tools/publish.py add results/official_v1_7_off --name pi5-off-official
python3 tools/publish.py build
```

A run counts as official when it records a `--setup`. In the README, its rows replace the exploratory rows (marked †) for the same model and thinking mode.
