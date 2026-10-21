# githi parity: the standalone agent vs the supervisor's githi runtime

The standalone `githi/` is a single-process agent (one Python loop, tools in-process).
The supervisor's githi is the same agent broken into manifest-pinned pieces:

| Standalone piece | Where it lives in the supervisor |
|---|---|
| `agent/loop.py` `run_loop` | `temporal/workflow.py` `_run_steps` (ordering, guards, approvals) + `temporal/steps.py` (one model call / one tool call per activity) |
| `agent/agent.py` `Agent` | `session/manager*.py` + the session workflow |
| `types.py` | `agent/events.py` (journal events), `models/messages.py` (wire types), `seams/core/types.py` |
| `harness/system_prompt.py` | prompt pack (`harness/prompt_packs/*.json`) through `CatalogPromptAssembler` |
| `harness/compaction.py` | `temporal/context_memory.py` + `seams/compaction` |
| `tools/*` | `supervisor/tools/builtin/*` (code) + `harness/tool_sets/*.json` (what the model is shown) |
| `llm/*` | `gateway/` + `models/` |
| `subagent/*` | `temporal/subagent_tools.py`, `session/subagent_ops.py`, `SubagentJobWorkflow` |
| `memory/*` | memory service (notebooks, recall) + `memory/tools.py` |

Comparing file sizes is meaningless; the comparison is **by capability**, each checked in code.

## Verdict per capability

Legend: **same** = equivalent or better · **gap** = weaker, fixed in this change · **later** = missing, out of scope here (reason given).

### The loop

| # | Capability | Standalone | Supervisor before | Verdict |
|---|---|---|---|---|
| L1 | Steps per run | 30 (`max_turns`) | **8, hard-coded** (`MAX_TOOL_ITERATIONS`); then the run ends as an error with no answer | gap |
| L2 | Turn-budget warning | last 3 turns get "[N turns remaining…]" on the request only | none | gap |
| L3 | Forced answer on the final turn | tools withheld + "FINAL turn" notice, so the run ends with an answer | none (error) | gap |
| L4 | Truncated reply (`finish_reason=length`) | tool calls refused with "reissue in a shorter response", loop continues | **calls executed** with whatever arguments survived | gap |
| L5 | Malformed tool-call JSON | refused with "not valid JSON, resend" | becomes `{}` / `{"raw": …}`; tool runs or fails with a misleading "missing field" | gap |
| L6 | Every tool call answered | synthetic results for un-run calls | model layer fills "No result provided" (`models/messages.py`) | same |
| L7 | Compaction between model calls | before **every** call | only at the start of a turn; a long tool-heavy turn hits `ContextWindowFull` | gap (step-level shrink of old tool output) |
| L8 | Durable execution / crash recovery | none | per-step journal, adoption, at-most-once tools | supervisor better |
| L9 | Loop guard, approvals, budgets | hooks only | manifest guards, durable approvals, cost budget | supervisor better |
| L10 | Abort | ESC between tools and stream chunks | abort signal cancels the in-flight activity | same |
| L11 | Steering mid-run / follow-ups | queues drained between turns | follow-ups queue as the next turn; no mid-run steering | later (needs a workflow signal into the step loop) |
| L12 | Streaming text deltas | yes | stepped turns use blocking `complete` | later (UX only; the journal is the stream) |
| L13 | Phase hooks (`prepare_next_turn` model/prompt swap) | yes | no | later (no manifest consumer yet) |

### Prompt

| # | Capability | Standalone | Supervisor before | Verdict |
|---|---|---|---|---|
| P1 | System prompt | pi's header, tool inventory, guidelines, cwd | `"coder\n\nYou are a coding agent.\n\nPrefer small diffs."` (the persona *label* printed as text, no cwd, no tools) | gap |
| P2 | Skills (progressive disclosure) | listing in prompt, model reads SKILL.md | `PromptBindings.skill_listing` exists, nothing fills it | later (needs a skills artifact kind in the catalog) |

### Tools (what the model is shown is the pinned tool set JSON)

| # | Tool | Standalone | Supervisor before | Verdict |
|---|---|---|---|---|
| T1 | edit | batch edits, CRLF/BOM kept, diff returned | builtin exists but **not in the githi tool sets**; single edit; no diff | gap |
| T2 | bash | timeout param, 20 KB cap, full output spilled to a file it names | timeout in code but **not in the schema**; 64 K chars per stream, no spill | gap |
| T3 | read | 2000 lines / 50 KB cap with "use offset=N" hint, binary detected | whole file returned, binary decoded as U+FFFD | gap |
| T4 | grep, ls, write | — | equivalent | same |
| T5 | Tool result as the model sees it | plain text | `json.dumps({"ok":…, "payload":…})` — file contents arrive escaped | gap |
| T6 | Tool descriptions | detailed, mechanical (pi's) | one-liners | gap |
| T7 | websearch / webfetch / image / ask_user | yes | no (bash + curl works; approvals cover "ask") | later (each needs a backend or UI contract) |

### Model layer, memory, subagents

| # | Capability | Verdict |
|---|---|---|
| M1 | Prompt caching, retries with backoff, cache-token usage, thinking levels, reasoning replay | same (in `models/` + `gateway/`) |
| M2 | Long-term memory | supervisor better (memory service, notebooks, recall, history tool) |
| M3 | Subagents: foreground, background, result | same |
| M4 | Fork-with-context and continuable children as *model* tools | later (the API has fork and child sessions; no model tool yet) |

## What this change fixes, and how each is verified

| Gap | Fix | Verified by |
|---|---|---|
| L1–L3 | `loop.max_steps` in the harness manifest (default 30); warning on the last 3 steps and a final step with no tools, both on the request only, never journalled | unit tests on the step request; workflow test that a run at the limit ends `stop` with an answer |
| L4 | a `length` reply's calls are journalled as refused (`TRUNCATED`), not run; the loop continues | steps test with a fake gateway returning `length` + a call |
| L5 | unparseable arguments are kept as a marker and refused (`MALFORMED_ARGUMENTS`) | test from the wire parse through the step |
| L7 | before each model step, if the request is over the compaction limit, the oldest tool outputs *in the request* are shrunk (journal untouched) | test that an over-limit run is sent under the window |
| P1 | prompt pack v1.1: pi's header and guidelines, `{cwd}` filled; the assembler appends a tool inventory from the tools actually offered | assembler tests; the live run's prompt |
| T1–T3, T6 | tool set v1.1 adds `edit`, full schemas and descriptions; read caps + binary; bash 20 KB cap with spill file; edit batch + line endings + diff | tool unit tests |
| T5 | tool results rendered as text for the model (`payload` as-is, errors as `error [CODE]: message`), with the error flag carried separately | conversation tests; model layer test that `is_error` survives |

Then: full suite, mypy, e2e stages, and the free-model live stage.
