# Official runs

This is the protocol behind the published Raspberry Pi 5 results. It uses suite 1.7.0 and these models: `qwen3.5:2b`, `qwen3.5:4b`, `gemma4:e2b` and `gemma4:e4b`.

## Plan

| | Thinking off | Thinking on |
|---|---|---|
| Invocation | one run with all the models | one run per model |
| What runs | three passes (`--passes 3`, the default), the model order rotating between passes | at 4 threads, three passes of the 65 cases (the default); at 2 threads, one pass (`--passes 1`) |
| Published | every pass | every pass |
| Phases | cold, warm, sustained | warm only |
| Expected time on the Pi | about 10 hours | from about 4.5 hours (`qwen3.5:2b`) upwards per model |

**Why three passes.** Three passes give a timing spread and a repeatability check at a cost that stays fixed as models are added. Before protocol 3, `--cycles 1` gave one pass per model in the run (4 with four models), and only the first 3 rounds were published, which is the same schedule as `--passes 3`. Outputs are close but not identical between passes: in the official run, 74–82% of cases repeated word for word, and the pass or fail result differed between passes for 2 of 260 model–case pairs. So the scores are averaged over the passes.

**Why three thinking-on passes at 4 threads, and one at 2.** A thinking-on pass takes 1–6 hours per model, so passes are spent where the main results are: the 4-thread runs, which the README's tables and charts use. The 2-thread runs only feed the thread comparison. The `lfm3` and `lfm3-vl:2b` runs follow this plan. The earlier 4-thread runs of `gemma4:e2b` and the Qwen models were single passes and are being re-run with three. In the three-pass runs so far, 69–77% of cases repeated word for word, and no case's pass or fail result changed between passes.

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

Run these from `~/light-eval` inside the tmux session, after setting `SETUP` in that same shell. These are the commands of the first official runs, at 2 threads, written for protocol 3: the original runs used `--cycles 1`, which `--passes` replaced (thinking off: `--cycles 1` with four models gave 4 rounds, of which the first 3 are published; thinking on: one pass). The runner's default is now 4 threads, so `--threads 2` is written out below to reproduce them; leave it out for 4-thread runs.

**Rehearsal first (about 10–15 minutes).** It runs every phase and the gates between two models on the arithmetic track only, to check the whole chain before committing a night to it:

```bash
python3 light_eval.py run --models qwen3.5:2b gemma4:e2b --tracks arithmetic --sustain-seconds 20 \
  --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/rehearsal_v1_7
```

It should end with a summary for both models, `manifest.json` should show `"status": "complete"`, and `thermal_gate_timeouts` should be empty, or show how long cooling takes. Don't publish it.

Thinking off: all four models, all phases, three passes.

```bash
python3 light_eval.py run --models qwen3.5:2b qwen3.5:4b gemma4:e2b gemma4:e4b \
  --passes 3 --threads 2 --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/official_v1_7_off
```

Thinking on: one model per run, warm phase only.

```bash
for model in qwen3.5:2b gemma4:e2b qwen3.5:4b; do
  python3 light_eval.py run --models "$model" --phases warm --thinking on --threads 2 --passes 1 \
    --temp-min 30 --temp-max 50 --setup "$SETUP" --out "results/official_v1_7_on_${model//[:.]/_}"
done
```

A run that stops early leaves a partial folder, and `--out` must name a new folder. To retry, give the retry a new name and keep the partial run for reference.

## Thread count

The first official runs used `--threads 2`, and Ollama's runner was started with `-t 2`, confirmed on the Pi with `ps`. The runner now defaults to `--threads 4`, all of the Pi 5's cores. Ollama on its own normally uses all physical cores, so these timings are probably slower than with Ollama's defaults. The thread count mainly affects prompt processing, which is limited by computation. Generation, which is limited mostly by memory bandwidth, should change less. Accuracy should stay within the normal variation between passes.

### 4 threads: protocol and outcome

The 4-thread runs repeated the official runs with `--threads 4` and nothing else changed. They left out `gemma4:e4b`, which hung the Pi with thinking off. They were chained in one tmux session:

```bash
python3 light_eval.py run --models qwen3.5:2b qwen3.5:4b gemma4:e2b --passes 3 --threads 4 \
  --temp-min 30 --temp-max 50 --setup "$SETUP" --out results/official_v1_7_off_threads4 ; \
for model in gemma4:e2b qwen3.5:2b qwen3.5:4b; do
  python3 light_eval.py run --models "$model" --phases warm --thinking on --threads 4 --passes 1 \
    --temp-min 30 --temp-max 50 --setup "$SETUP" --out "results/official_v1_7_on_threads4_${model//[:.]/_}"
done
```

Once a model had loaded, `ps -ww -o args= -C llama-server | tr ' ' '\n' | grep -x -A1 -- '-t'` printed `4`. All four runs completed with no gate timeouts, no swap, no out-of-memory kills and no throttling. The whole chain took about 17 hours.

**Outcome** (see the comparison table under the [results](../README.md#results)):
- **`gemma4:e2b` gets faster at everything:** prompt processing +67%, generation +12–15%, and 31–40% less time per case, with thinking off and on.
- **The Qwen models generate 11–14% slower with 4 threads.** `qwen3.5:4b` still gains 66% in prompt processing, so with thinking off it is 32% faster per case, but with thinking on it is 13% slower. `qwen3.5:2b` gains nothing in prompt processing (−8 to −10%).
- **Accuracy is unchanged with thinking off.** With thinking on, Qwen timeouts fell from 11 to 9 and from 13 to 11. That is a single pass each, within the usual variation between passes.
- **Temperature rose by 3–12 °C,** to peaks of 72–78 °C, close to the roughly 80 °C throttling point, though no case throttled.

**Decision.** At first the main results stayed at 2 threads. Since 2026-11-22 they use 4 threads, the runner's default, because:
- 4 threads matches how Ollama is normally run, using all cores, and the bottom-line recommendation;
- with thinking off, accuracy is the same at 2 and 4 threads.

The 2-thread runs stay published as the comparison. `gemma4:e4b` has only its 2-thread run, because rerunning it risks another hang, so it appears in the main results marked "2 threads only". For the Qwen models, 4 threads slows generation, so long reasoning gets slower. That is a property of the models on this hardware, and the comparison table shows it. Two possible explanations, both untested: generation is limited by memory bandwidth, which the cores share; and the Qwen3.5 architecture, which mixes attention with recurrent layers, may do too little work per token to split well across 4 cores.

## What happened in the official thinking-off run

The Pi hung and rebooted in round 4, after 49 of `gemma4:e4b`'s 65 cases, because memory ran out. Rounds 1–3 had completed for all four models, so those were published with `--rounds 1-3`. The published manifest records the reason, and the run's own `"status": "running"` is kept as it was. See [the results](../README.md#results).

## Publishing

Copy each finished run folder to the Mac's `results/`, then:

```bash
python3 tools/publish.py add results/official_v1_7_off --name pi5-off-official
python3 tools/publish.py build
```

Publish thinking-off runs with `--rounds 1-3`, whatever the number of models. If a run stops partway, publish only its complete rounds the same way, and add `--note "why"` when readers need to know. `--flag "MODEL=text"` shows a warning in that model's memory column.

A run counts as official when it records a `--setup`. In the README, its rows replace the exploratory rows (marked †) for the same model and thinking mode.
