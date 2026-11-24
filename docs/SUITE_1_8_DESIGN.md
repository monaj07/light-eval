# Suite 1.8 and protocol 4: design

Status: **agreed in outline, 2026-11-25**; cases not yet written. The decisions taken are listed at the end.

## Why a new suite

Suite 1.7 has done its job, and the published results show where it falls short:

- **The top is saturated.** Four of the five tracks have a result at 100% (arithmetic, structured and agent three times each, vision once), and `lfm3` with thinking on passes every control case. Only context still has headroom (best 77%). When the best models tie at 100%, the suite can no longer say which is better.
- **Too many cases are rudimentary.** "What is 173 × 29?", "How many blue circles are shown?" and a one-field JSON extraction test whether a model works at all, not whether it is good. The vision track in particular leans on counting basic shapes.
- **The suite misses common edge uses:** answering from local documents (RAG), choosing among many tools (as MCP servers expose them), following formatting instructions, resisting instructions planted in documents or tool results, and writing code.
- **65 cases give wide intervals.** One case is 1.5 points; a 94% score is consistent with roughly 85 to 98%.

### What the passes showed

| Mode | Outputs identical between passes | Pass or fail changed between passes |
|---|---|---|
| Thinking off | 74–82% of cases | 2 of 260 model–case pairs |
| Thinking on | 18–77% of cases | 0 to 6 of 65 cases per model (`qwen3.5:4b`: 39 to 43 passed per pass) |

Repeated passes matter more with thinking on than off. Even so, the uncertainty that comes from *which* cases are in the suite is larger: for `qwen3.5:4b` (63%), the spread between passes is about 2 cases (3 points), while the binomial spread from having only 65 cases is about 6 points. More distinct cases reduce the larger of the two.

## Goals

1. Restore headroom: the best model should score about **50–70% on the hard tier**, with no track saturated.
2. Replace toy tasks: every case, controls included, is a realistic task.
3. Cover the edge uses above, including code, without an LLM judge and with the standard library only.
4. Keep each official run within the Pi time that suite 1.7 used, by running **one pass of a much larger suite** instead of three passes of a small one.
5. Show uncertainty honestly with a single pass.

Not a goal: a general-knowledge benchmark (the makers already publish those), or longer prompts for their own sake.

## Suite structure

Eight tracks of 20 cases. Seven have three tiers: **controls** catch basic failures (format, refusals) and are reported separately; **challenges** are about suite 1.7's challenge level; **hard** cases are new and set the headroom. The code track is hard throughout.

| Track | Controls | Challenges | Hard | Total |
|---|---:|---:|---:|---:|
| Arithmetic | 3 | 7 | 10 | 20 |
| Structured | 3 | 7 | 10 | 20 |
| Agent (with large tool catalogues) | 3 | 7 | 10 | 20 |
| Vision | 3 | 7 | 10 | 20 |
| Context | 3 | 7 | 10 | 20 |
| **RAG** (new) | 3 | 7 | 10 | 20 |
| **Instructions** (new) | 3 | 7 | 10 | 20 |
| **Code** (new) | – | – | 20 | 20 |
| **All** | **21** | **49** | **90** | **160** |

**Hard means more reasoning, not longer prompts.** Long prompts mostly measure prompt reading, which on the Pi is already the slowest step and the main cause of thinking-on timeouts. Hard cases add steps, distractors and constraints. Prompt length stays at suite 1.7 levels except where the track is about length (context, RAG, large tool catalogues), and there it is capped at the current long-context size.

**Injection resistance** is not a separate track. Five hard RAG cases and five hard agent cases contain a planted instruction (in a retrieved passage, or in a tool result). Passing means ignoring it. The site reports these ten as their own **injection** metric across both tracks.

## Retiring rudimentary cases

Every suite 1.7 case is reviewed against one rule: **a case stays only if it is a realistic task someone would give a local model.** Bare calculator sums, counting coloured shapes and one-field extractions go. Controls are kept easy but real: one clear fact from a receipt, a single tool call that does something useful, one constraint on a short text. Cases that survive are reworded, so a model can't have seen the 1.7 wording, and assigned to a tier by the calibration below rather than by their 1.7 label.

## The tracks

Every case keeps suite 1.7's rules: a typed answer compared exactly (with a tolerance where stated), the answer type derived from the answer's type only, and no prompt that leaks its answer.

### Arithmetic
- **Controls:** one realistic calculation from a short scenario (a bill split, a unit price).
- **Challenges:** two- or three-step word problems, unit and time conversions, percentages applied in sequence.
- **Hard:** four to six steps with distractor numbers, dates across month and leap-year boundaries, rates combined over different periods, problems where the obvious shortcut gives a wrong answer.
- **Scoring:** exact number, with a stated tolerance for division results.

### Structured
- **Controls:** a few typed fields from a realistic message.
- **Challenges:** nested extraction, arrays in source order, `null` for missing fields.
- **Hard:** several records with conflicting information (take the latest), values normalised to a given format (ISO dates, cents, enumerations), conditional fields ("include `discount` only if…"), schemas with enumerated values.
- **Scoring:** exact JSON comparison, integers required where the answer is an integer.

### Agent (with large tool catalogues)
- **Controls:** one useful call and its result.
- **Challenges:** suite 1.7's style: 2 to 5 tools, ordered calls, final state checked.
- **Hard:** **20 to 40 tools on offer** with similar names and overlapping descriptions (as several MCP servers would expose them), 6 to 10 calls with branching on tool results, an injected instruction in a tool result in five cases, and a tool that fails once and must be retried in others.
- **Scoring:** as now: tool use (calls, arguments, order, final state) and final answer, reported separately and together.
- **Why it matters on the Pi:** every tool definition is part of the prompt, so a large catalogue costs prompt-reading time on every turn. The track measures whether a model picks the right tool and what a big catalogue costs in latency.

### Vision: realistic images instead of shapes
All images stay synthetic and deterministic, drawn by `fixtures/make_fixtures.py`, but they show the kinds of things people photograph or screenshot. Counting coloured shapes is retired.

| Image type | Challenge example | Hard example |
|---|---|---|
| Receipts and invoices | the total on a receipt | the pre-tax total of only the discounted items |
| Charts | a bar's value | the difference between two series at a given month, read from a legend |
| Tables in images | one cell | the row matching two conditions, across merged header cells |
| Dashboards and app screens | which toggle is on | which setting conflicts with the warning shown |
| Diagrams | the next step in a flowchart | the end state after following branches with given inputs |
| Floor plans and maps | which room is next to the kitchen | the shortest route through doors, in rooms passed |
| Gauges and dials | an analogue clock's time | a gauge reading converted to another unit |
| Signs and labels | a printed code | small or rotated text, with a similar-looking decoy |

Controls read one clear value from the same kinds of image.

### Context
- **Controls:** one fact in a short log.
- **Challenges:** suite 1.7's long-log retrieval and revision cases.
- **Hard:** multi-hop joins, the latest valid state over revisions, negation and exclusions, at the current maximum length.
- **Scoring:** exact answer.

### RAG (new)
Each case gives a question and 4 to 8 short passages from a deterministic synthetic corpus (product manuals, policies, logs), each with an ID. The model answers with a JSON object `{"answer": …, "sources": [IDs]}`.

- **Controls:** the answer is stated in one passage.
- **Challenges:** combine two passages; ignore passages that look relevant but are about another product or version.
- **Hard:** conflicting passages where the newest or the authoritative one wins; questions the passages don't answer, where the correct answer is `null` (the model must not guess); five cases with an instruction planted in a passage.
- **Scoring:** the answer must match exactly, and the cited sources must be exactly the passages needed. Answer and citation are reported separately (like the agent track) and together.

### Instructions (new)
Free-text tasks with constraints checked by rules, in the style of IFEval: number of bullet points or sentences, word-count ranges, required and forbidden words, a fixed ending, casing, sections in a given order, JSON keys in a given order.

- **Controls:** one constraint.
- **Challenges:** two or three constraints.
- **Hard:** four or five constraints that interact (for example "exactly three bullets, each under twelve words, none using the letter 'z', ending with 'Done.'").
- **Scoring:** every constraint has a checker function; a case passes only if all pass. With thinking on, only the final answer is checked, never the reasoning.

### Code (new, all hard)
Twenty hard tasks from eight categories. Each asks for one Python function or class (or, for SQL, one query) with a stated name and signature, and is checked by running hidden tests.

| Category | Cases | Example |
|---|---:|---|
| Algorithms and data structures | 4 | merge intervals with touching ends and a tie-break rule; topological order with lexical tie-breaks |
| Parsing | 3 | evaluate arithmetic with precedence and unary minus; compare version strings with pre-release tags |
| Bug fixing | 3 | a given function fails on stated inputs; return a corrected version |
| Dates and money | 2 | add business days across given holidays; split an amount into shares that sum exactly, distributing the remaining cents |
| Data transformation | 3 | group and aggregate nested records; describe the difference between two nested dictionaries in a given format |
| SQL | 2 | a query over a small SQLite schema, run with Python's built-in `sqlite3`; rows compared exactly |
| Regular expressions | 1 | a pattern that matches a specification, tested on lists that must and must not match |
| Specification with state | 2 | a rate limiter or state machine with a given interface and edge cases |

- **Answer format:** one fenced code block. The runner takes the first `python` (or `sql`) block, or the whole answer if there is none.
- **Scoring:** the case passes only if every hidden test passes. The prompt shows one or two examples; the hidden tests cover the edge cases the task names. Failures are reported by kind: `wrong_answer` (a test failed), `invalid_code` (syntax error, or the named function is missing), `code_error` (an exception), `code_timeout`.
- **Determinism:** fixed tests, no randomness, exact comparison (or a stated tolerance for floating point).
- **Running model-written code safely:** the code runs in a separate `python3 -I` process in an empty temporary folder, with an empty environment and, on Linux, limits on CPU time (10 s), memory (512 MB), file size and process count. Before it runs, a syntax-tree check rejects imports outside a short allowlist (`math`, `itertools`, `collections`, `functools`, `re`, `heapq`, `bisect`, `datetime`, `decimal`, `fractions`, `json`, `string`, `typing`, `dataclasses`, plus `sqlite3` for SQL cases) and calls such as `open`, `exec`, `eval` and `__import__`. A syntax check is not a complete sandbox, so code cases run only on the dedicated benchmark Pi, and the methodology says so.
- **Checking the checker:** each case ships with a reference solution that passes every test and two or three plausible wrong solutions (an off-by-one, a missed edge case) that each fail at least one. Tests enforce both, so a test set that would pass a wrong answer is caught before the suite is fixed.
- **On the Pi:** running the tests takes well under a second per case; the cost is in generation, since code answers are longer than suite 1.7's typed answers.

## Difficulty calibration

1. **Write** each case with its expected answer (or reference solution and tests) and, for hard cases, the steps that make it hard.
2. **Blind solve:** a fresh subagent that sees only the exported prompts, passages and images must answer every case correctly before it is committed, as now. Agent cases stay excluded because they need the simulator; RAG and code cases are included (a code answer counts when it passes the hidden tests).
3. **Mac pilot:** run the full suite once on the Mac with the same model builds, thinking off and on. This takes hours instead of days.
4. **Adjust to the targets:**

| Tier | Target on the Mac pilot |
|---|---|
| Controls | most models pass at least 90% |
| Challenges | between 40% and 80% for the mid-range models |
| Hard (code included) | best model 50–70%; no case that every model passes |

A hard case that every model passes moves down a tier (or, in the code track, is made harder); one that no model passes and that the blind solver found ambiguous is rewritten. Calibration happens before the suite version is fixed, so official runs never use a moving suite.

5. **Time check:** with thinking on, a strong model should solve any hard case in well under the 10-minute limit on the Pi (about 4,000 thinking tokens at most). A case that only times out measures speed, not ability.

## Protocol 4

- **One pass by default** (`--passes 1`), at 4 threads. No 2-thread runs: suite 1.7 answered the thread question, and its comparison stays published.
- **A repeatability sample:** every official run repeats a fixed, stratified 15% of the cases (24 of 160, spread across tracks and tiers) once more. **Scores come from the first pass only;** the second attempt gives each run a measured flip rate ("2 of 24 repeated cases changed"), shown on the site as a reliability figure. This keeps every case equally weighted in the score while still measuring how much a single pass can wobble.
- **Intervals instead of pass ranges:** each accuracy gets a 95% Wilson interval from its number of cases. Ranks tie as now (a result's rank is 1 plus the number of results whose interval lies entirely above it).
- **Publishing:** `run_problems` accepts one pass and requires the repeatability sample in official runs.

## Pi time budget

One pass of 160 cases, at suite 1.7's mean time per case (4 threads):

| Model | Thinking off | Thinking on |
|---|---:|---:|
| `gemma4:e2b` | 0.8 h | 3.1 h |
| `lfm3` | 0.8 h | 4.3 h |
| `lfm3-vl:2b` | 0.8 h | 5.0 h |
| `qwen3.5:2b` | 0.8 h | 11.2 h |
| `qwen3.5:4b` | 1.6 h | 14.6 h |
| `lfm3-omni:2b`, LFM2.5, `gemma4:e4b` | 0.2–2.3 h | none |

Add 15% for the repeatability sample and the cooling between models: about **44 hours with thinking on** for the five thinking models, and **about 9 hours with thinking off**. Suite 1.7 took about 47 hours for its three 4-thread thinking-on passes, plus about 11 for its 2-thread runs, which 1.8 drops. Hard cases and code answers will take longer per case than the 1.7 average, so these are lower bounds: the Qwen models with thinking on already reach the 10-minute limit on 10 to 14 cases per 65, and each timeout costs the full 10 minutes.

## What changes in the code and on the site

- `cases.py`: the new and reworked cases, the `hard` tier, the RAG corpus generator, the instruction checkers, the code runner and its tests, and the injection flag. `VERSION = "1.8.0"`.
- `fixtures/make_fixtures.py`: the new realistic images; the shape images retire with suite 1.7.
- `light_eval.py`: protocol 4 (`--passes 1` default, the repeatability sample), larger tool catalogues in the agent loop.
- `tools/publish.py` and `tools/build_site.py`: three tiers, eight tracks, the injection metric, Wilson intervals, the repeatability rate.
- README and site: the heatmap gains three columns and a tier split; the leaderboard shows intervals instead of pass ranges; a note explains the suite change.
- **Suite 1.7 archive:** its results stay in `docs/results/`, and the site keeps a "Suite 1.7" view of them after the switch, labelled as the previous suite. The README shows only the current suite, as `build` does now.
- Tests: the suite counts and per-tier counts, every checker, the corpus generator's determinism, the code runner's limits and the reference and wrong solutions of every code case, the injection scoring, and that no prompt leaks its answer.

## Rollout

1. Agree this design.
2. Review the suite 1.7 cases and retire the rudimentary ones.
3. Write the cases track by track, each through the blind solve, with tests; the code runner and its safety checks come first, since the code track depends on them.
4. Mac pilot and calibration; fix the suite.
5. Protocol 4 and the publishing and site changes, including the suite 1.7 archive view.
6. Rehearsal on the Pi (one model, a few tracks), then the official runs: thinking off first (one night), then thinking on per model.
7. Publish and switch the README and site to suite 1.8.

## Decisions

| Question | Decision |
|---|---|
| Cases per track | 20 (160 in all) |
| Repeatability sample | reported as a flip rate, not averaged into the score (recommended; confirm) |
| 2-thread runs | dropped for 1.8 |
| Code track | included now, all hard, checked by running tests |
| Suite 1.7 on the site | kept as an archive view after the switch |
| Rudimentary 1.7 cases | retired; every case must be a realistic task, and vision moves from shapes to realistic images |
