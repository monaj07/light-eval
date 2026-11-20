# Failure analysis

This page walks through representative failures from each track. It shows what the models actually answered, and what that says about both the models and how Light Eval scores them.

All examples come from the official Raspberry Pi 5 run with thinking off (suite 1.7.0, [`docs/results/pi5-off-official`](results/pi5-off-official)), round 1 of 3. The [README](../README.md#results) has the overall scores and [METHODOLOGY.md](METHODOLOGY.md) has the full scoring rules. To see any case for yourself:

```bash
python3 tools/show_case.py docs/results/pi5-off-official json-24h-times
```

## How a case is scored

- **No LLM judge.** Each reply is parsed as JSON and compared with the expected value, with types and list order both mattering. `"S4"` is not `{"sensor_id": "S4"}`, and `1` is not `"Q1"`.
- **Forced answer format.** Arithmetic, vision and context replies must be an `{"answer": …}` object, which Ollama enforces through a schema.
  - With thinking off, this means the model commits to an answer immediately, without working.
  - The structured-output track sends no schema, so it tests whether a model can produce exact JSON by itself. One surrounding Markdown code fence is accepted.
- **Agent cases are checked twice:**
  - **tool use:** the right calls, arguments, order and final state;
  - **final answer:** the right value.

  A case passes end to end only if both are right.

The failures below fall into three kinds, which the scorer reports separately:

| Kind | Status | Example |
|---|---|---|
| Wrong content | `wrong_answer` | a wrong number, ID or count |
| Wrong form | `invalid_json`, or `wrong_answer` for the wrong shape | `[Q1, Q3]` without quotes; objects instead of strings |
| Wrong process | `wrong_tool_trace`, `tool_limit` | the right answer reached through extra, invented or forbidden tool calls |

## 🔢 Arithmetic: no room to work

With thinking off, every model fails some control cases:

| Case | Prompt (short) | Expected | qwen3.5:2b | qwen3.5:4b | gemma4:e2b | gemma4:e4b |
|---|---|---:|---:|---:|---:|---:|
| `math-units` (control) | seconds in 2 hours 17 minutes | 8220 | 139 | 8200 | 7500 | 7320 |
| `math-remainder` (control) | remainder of 374 ÷ 17 | 0 | 12 | 10 | 13 | 14 |
| `math-clock-angle` | smaller angle between the hands at 7:38 | 1 | 10.67 | 104 | 82.8 | 23.5 |

- **The answers look like guesses, not working.** qwen3.5:2b's 139 is 2 × 60 + 17, the time in minutes rather than seconds. Every remainder answer is non-zero, although 374 = 17 × 22.
- **The clock case needs several steps.** At 7:38 the hour hand has moved 19° past the 7, to 229°, while the minute hand is at 228°, so the hands are 1° apart.
- **Thinking on fixes most of this.** On the Pi, `gemma4:e2b` passes all 13 arithmetic cases with thinking on, `qwen3.5:4b` 11 and `qwen3.5:2b` 10. Every remaining failure is a timeout, not a wrong answer. See [With thinking on](#with-thinking-on).

## 🧾 Structured output: one case, three different mistakes

**`json-24h-times`** (challenge). Prompt:

> Meetings: Room B at 1:05 pm, Room A at 9:40 am, Room C at 12:15 am. Return only a JSON array of objects with keys "room" (the room letter as a string) and "time" (a 24-hour "HH:MM" string), sorted from earliest to latest time of day.

Expected: `[{"room": "C", "time": "00:15"}, {"room": "A", "time": "09:40"}, {"room": "B", "time": "13:05"}]`

The trap is **12:15 am**. On a 12-hour clock the hour after midnight is written "12", so 12:15 am is 00:15, the earliest meeting of the day.

| Model | Answer (order, room and time) | Result | What went wrong |
|---|---|---|---|
| gemma4:e4b | C 00:15, A 09:40, B 13:05 | ✅ | — |
| gemma4:e2b | A 09:40, **C 12:15**, B 13:05 | ❌ | Copied 12:15 unchanged. The sort is consistent with that wrong time. |
| qwen3.5:2b | A 09:40, B 13:05, **C 00:15** | ❌ | Converted every time correctly, but didn't sort: C is left last. |
| qwen3.5:4b | **Room A** 09:40, **Room C** 00:15, **Room B** 13:05 | ❌ | Wrote `"Room A"` instead of the letter, and didn't sort. |

The same pattern shows elsewhere in the track:

| Case | Expected | Typical failures |
|---|---|---|
| `json-filter-sort`: IDs with a reading above 50, highest first (S1=48, S2=73, S3=51, S4=90, S5=50) | `["S4","S2","S3"]` | `["S4","S2"]` drops the edge case S3 = 51; `["S4","S2","S3","S1"]` includes 48; `[{"sensor_id":"S4"},…]` uses its own shape |
| `json-list` (control): IDs whose status is active | `["Q1","Q3"]` | `[Q1, Q3]` is invalid JSON (unquoted); `[1, 3]` gives numbers instead of IDs |

## 🛠️ Agent: right answer, wrong process

In all three examples below, the model gave the **correct final answer** but failed on tool use.

**`agent-direct`** (control). The prompt says: *Reply with only the JSON object `{"answer":"ready"}`. Do not call any tools.* qwen3.5:4b replied `ready`, but first called `set_indicator(color="green")` and `calculate(10 + 20)`. In a real system, the first call changes something.

**`agent-no-op-state`** (challenge). The task: read the indicator once, and set it to green only if it isn't green already. It already was.

| Model | Tool calls | Result |
|---|---|---|
| qwen3.5:2b | `read_indicator` | ✅ |
| gemma4:e2b, gemma4:e4b | `read_indicator`, `set_indicator(green)` | ❌ redundant write |
| qwen3.5:4b | `read_indicator`, `calculate(1 + 0)`, `set_indicator(green)` | ❌ unrequested calculation and redundant write |

**`agent-count-delivered`** (challenge). The task: look up two customers' orders and count the delivered ones. The order IDs have to come from `find_order`.
- **qwen3.5:2b** invented IDs (`read_order("miras_order")`), received errors, and answered 0. It fails on both process and answer.
- **qwen3.5:4b** also invented IDs at first (`12345`, `67890`), then read the real ones and answered 1, which is correct. But it made 8 calls where 4 were requested, so it fails on process.

This is why qwen3.5:4b passes only 3 of 13 agent cases with thinking off, against 11 for qwen3.5:2b. Its final answers are mostly right, but it adds calls nobody asked for. The README reports tool use and final answers separately for this reason.

## 👁️ Vision: counting and reading

| Case | Expected | Answers |
|---|---|---|
| `vision-count-mixed` (control): orange squares | 4 | 3 from qwen3.5:2b, gemma4:e2b and gemma4:e4b; only qwen3.5:4b said 4 |
| `vision-ocr-label` (control): the large code on a label | `K7M-42` | `KVM-42` (qwen3.5:2b), `KMA-2` (qwen3.5:4b); gemma4:e2b read it correctly |
| `vision-table-total` (challenge): sum of QTY × PRICE in a table image | 12.25 | 10.75, 10.55, 7.15 and 34.15; no model got it |

The table case needs two skills at once: reading every cell, then doing the arithmetic. A slip in either fails it. The OCR misses (`7` read as `V`) suggest the Qwen models are weaker at reading text in images than the Gemma models.

## 📜 Context: needles in a 96-record log

**`context-long-revision`** (challenge). *For the exact relay ID TX-52, find the shelf in its highest numeric revision whose state is approved. Ignore pending and cancelled revisions.* The relevant records, among 96:

```
ASSIGN relay=TX-52 revision=4  state=approved  shelf=S277   (first TX-52 record)
ASSIGN relay=TX-52 revision=9  state=pending   shelf=S801   newer, but not approved
ASSIGN relay=TX-52 revision=10 state=cancelled shelf=S244   newer, but cancelled
ASSIGN relay=TX-52 revision=7  state=approved  shelf=S851   ← the answer
ASSIGN relay=TX-52 revision=1  state=approved  shelf=S971   (last TX-52 record)
… plus 91 records for TX-520 to TX-528, which share the "TX-52" prefix
```

Every model fell for a different decoy:

| Model | Answer | Decoy |
|---|---|---|
| qwen3.5:2b | S277 | the first TX-52 record |
| qwen3.5:4b | S971 | the last TX-52 record |
| gemma4:e2b | S188 | TX-527 revision 89: the highest revision, but the wrong relay |
| gemma4:e4b | S144 | TX-528, also the wrong relay |

**`context-long-absence`** (challenge). *What shelf is assigned to the exact relay ID TX-67? Use only ASSIGN records; COMMENT records are not evidence. If no assignment exists, answer "not found".* The only TX-67 record is `COMMENT relay=TX-67 shelf=S901 (not an assignment)`, so the answer is `"not found"`.
- Three models answered correctly.
- gemma4:e2b answered `S100`. That's the shelf of the TX-670 record directly above the comment: it found the right place, then took the wrong record.

The context cases are built so that shortcuts fail: the first match, the last match, a matching prefix, a nearby record, or an answer for an ID that isn't there. A wrong answer usually shows which shortcut the model took. The absence cases also check that a model will say "not found" rather than guess. Always answering "not found" doesn't pay either, because most context cases do have an answer.

## With thinking on

The official thinking-on runs (one pass per model; `gemma4:e4b` excluded, since it didn't reliably fit) were tested on the same cases. Here is how the cases on this page fared:

| Case | qwen3.5:2b | qwen3.5:4b | gemma4:e2b |
|---|---|---|---|
| `math-units`, `math-remainder` | ✅ ✅ | ✅ ✅ | ✅ ✅ |
| `math-clock-angle` | ⏱️ timeout | ✅ | ✅ |
| `json-24h-times`, `json-filter-sort` | ✅ ✅ | ✅ ✅ | ✅ ✅ |
| `agent-direct` | ✅ | ✅ | ✅ |
| `agent-no-op-state` | ⏱️ timeout | ⏱️ timeout | ✅ |
| `vision-table-total` | ✅ | ✅ | ✅ |
| `context-long-revision` | ⏱️ timeout | ⏱️ timeout | ❌ wrong answer |
| `context-long-absence` | ⏱️ timeout | ⏱️ timeout | ✅ |

- **The reasoning failures disappear:** the wrong unit, the remainder, the midnight conversion, the boundary in the filter, and the table arithmetic.
- **What's left is mostly time.** Each timeout reached the 10-minute case limit while the model was still thinking. The Qwen models think longest, and the long-context cases are hardest, because the 96-record log takes minutes to read before any thinking starts.
- **`context-long-revision` is still the hardest case:** `gemma4:e2b` finished, but still answered wrongly.

To see a thinking-on case, including the model's reasoning, use the thinking-on run folder:

```bash
python3 tools/show_case.py docs/results/pi5-on-gemma4-e2b context-long-revision --thinking
```

## Repeat passes

Each model answered every case three times. 74–82% of cases produced word-for-word the same output each time, depending on the model. Pass or fail differed between passes for only 2 of 260 model–case pairs:
- **`json-sort`, qwen3.5:4b:** in round 1 it returned `[{"id": "G03"}, …]` instead of `["G03", …]`, then the correct array in rounds 2 and 3.
- **`context-short-join`, gemma4:e4b:** correct (`S714`) in round 1, then `S559` in rounds 2 and 3.

The README averages scores over the passes, which is why a few track scores are fractions, such as 9.3. The examples on this page are from round 1. Use `--round 2` or `--round 3` with `tools/show_case.py` to see the others.
