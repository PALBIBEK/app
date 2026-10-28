# githi parity: pi's agent, made durable in the supervisor

The goal: every session pinned to a composition runs githi as well as pi runs, and as
durably as Pi Durable runs pi. The standalone `githi/` folder (pi-shaped) is a
**reference for behaviour only**: nothing in the supervisor imports it; each piece is
rewritten here with its own tests. The plan and its reasoning are in the design doc
"Pi Durable-grade agent loop for the supervisor"; this file is the status, checked in code.

## Where each piece lives

| Pi / Pi Durable piece | In the supervisor |
|---|---|
| The run loop (pi's generation task: request, tool round, next turn) | `temporal/workflow.py` `_run_phases`: phases over a small `RunState` (`temporal/contracts.py`), replayed by Temporal |
| One model call / one tool call (Pi Durable's task effects) | `temporal/steps.py` activities, journaled with keys before they return |
| The harness: prompt, what the model reads, the run's rules | `agent/runtimes/githi/`: `prompt.py`, `render.py`, `classify.py` |
| Transcript entries | the journal (`agent/events.py`) |
| Tools, prompt sections, models, compaction, subagents | the seams, bound to the session's pinned composition |
| What a conversation runs with (model, tools, instructions) | the pinned composition: harness version, prompt pack, tool set, memory profile |

## Status

Legend: **done** (phase that did it) · **next** (phase that will) · **same** (already equivalent) · **better** (the supervisor goes further).

### The loop

| # | Capability | Status |
|---|---|---|
| L1–L3 | Step limit from the pins (new agents: 200; pi has none, so a high limit only stops a run that never ends), "N steps left" on the last requests, a final step with no tools | same |
| L4–L5 | A cut-off reply's calls and malformed arguments are refused, not run | same |
| L6 | Every tool call answered | same |
| L7 | Compaction before any model call that needs it (`COMPACT` activity, keyed by step), and recovery from a provider's context overflow: compact once, ask again, else end the run saying why. The run's request is kept verbatim beside the summary, and the tool round the model has not seen yet is kept whole when it fits | done (phase 3); background compaction later |
| L20 | Compaction as pi's: triggered past the window less 16384 tokens (the default memory profile 1.1.0, and agents published from the builder); the newest 20000 tokens kept verbatim (widened to the latest round when that is larger); the summarizer reads pi's transcript (tool calls as `name(key=value)`, tool results cut at 2000 characters, thinking left out) with pi's prompts; the summary is framed as pi frames it ("The conversation history before this point was compacted into the following summary: <summary>…") and ends with the files the summarised turns read and changed (`<read-files>`, `<modified-files>`), carried from summary to summary | done (loop parity C) |
| L8 | Durable execution: per-step journal, adoption, at-most-once tools | better |
| L9 | Loop guard, durable approvals, cost budget | better |
| L10 | Abort | same |
| L11 | Steering: a message sent with `steer` while a run works joins it before its next step, once the round under way has finished (pi's loop places it there); one sent as the model answers continues the run; one that arrives as the run ends runs next as a follow-up | done (phase 6) |
| L12 | Streaming: the reply and its thinking are shown as the model writes them (`partial_output` on the event stream, written a few times a second outside the journal, never replayed, removed once journalled), from whichever worker thinks; tool output streams later | done (phase 6) for text and thinking |
| L13 | The loop as explicit phases over a checkpoint | done (phase 2) |
| L14 | Retryable model errors (429, 5xx, overload, dropped connection) wait on a durable workflow timer and are asked again: `Retry-After` honoured, 3 retries doubling from 2 s, each journalled as `ModelRetry`; an abort ends the wait; a refused request (401, bad body) ends the run at once instead of five blind attempts. A step whose worker keeps dying stops after its activity attempts, and the run ends saying so | done (phase 4); the retries and their waits are pinned in the harness `loop:` block (phase 7) |
| L16 | A run time limit pinned in `loop:` (`max_run_seconds`, "Time limit per run" in the builder): past it the run ends at its next step, saying so; the session goes on | done (phase 7) |
| L22 | pi-temporal's bounds: a hard deadline per run (`loop.hard_run_seconds`, "Hard deadline per run") stops the run where it is, mid-call, its calls answered as unknown (`RunDeadline`); tokens per run (`budget.max_tokens_per_turn`) and per session (`budget.max_tokens_per_session`, "Tokens per session") counted by the workflow from every model step, whichever worker ran it (they were counted in one worker's memory, so a model step elsewhere was missed and a busy turn could hold back the next one): past them the run ends (`RunTokenLimit`, `SessionTokenLimit`) and the session goes on; a session past its bound asks the model nothing for a new message | done (pi-temporal gaps E) |
| L17 | A sandbox rebuilt from its checkpoint after its worker or container died: the calls whose changes came after that checkpoint are journalled (`WorkspaceRestored`) and named to the model after the round, so it redoes them; an adopted, still-living sandbox keeps its newer files | done (phase 5, 7) |
| L18 | Tool-call ids unique in the run: a model reusing an id ("call_0" every step) or sending none is renamed before the reply is journalled, so a call is never answered with an earlier call's result | done (phase 7) |
| L19 | Chaos: one multi-step run with its worker SIGKILLed four times finishes once, every step journalled once, the files what the steps wrote (`test_githi_chaos.py`, the pi-temporal demo's pattern) | done (phase 7) |
| L15 | A step's calls as one round: the loop guard and every approval before any call runs; then every call at once, each its own `session.tool_call` activity (pi's parallel tool execution, pi-temporal's stepped mode), except that a call naming a file waits for the earlier calls that change it (pi's file mutation queue) and the memory and subagent tools keep their order; results are read back in the order asked. Replay per tool: at-most-once tools are never re-run | done (phase 5; parallel in pi-temporal gaps D) |
| L21 | A tool call that is lost (its worker died, its activity failed) is answered with pi's unknown outcome ("The outcome of this tool call is unknown … Check the current state before you try again.") and the run goes on, as pi-temporal's recovery seal does, instead of ending the run; a call's start and end are atomic claims (pi-temporal's dispatch claim), so a call closed unrun never starts late and has one result; a stop answers every call the run's model asked for: unknown for one that started, "This tool call did not run. The turn stopped before it started." for the rest; a change queued behind a lost change to the same file is not run | done (pi-temporal gaps D) |

### Prompt

| # | Capability | Status |
|---|---|---|
| P1 | pi's structure: preamble (the pinned pack), `<tools>`, `<rules>`, `<cwd>`; stable, so the provider's cache hits | done (phase 1) |
| P2 | Skills listed in the prompt | later (needs a skills artifact kind in the catalog) |
| P3 | The project's own instructions: the first of `AGENTS.override.md`, `AGENTS.md`, `CLAUDE.md` … in the workspace root, read as each run begins (from the live sandbox, else the saved files), in `<project_context>` before `<cwd>` | done (loop parity A) |
| P4 | Tools listed and offered in pi's order (read, bash, edit, write, grep, find, ls), others after; no persona label line above the preamble | done (loop parity A) |
| P5 | A failed tool result reaches the provider flagged as an error (Anthropic `is_error`), as pi sends it | done (loop parity A; lost when results became text in phase 1) |
| P6 | Thinking at medium for every model unless pinned, pi's default | done (loop parity A) |

### Tools

| # | Tool | Status |
|---|---|---|
| T1 | edit: pi's arguments (`path`, `edits[{oldText, newText}]`; a JSON-string or single-object `edits` accepted, as pi's `prepareArguments`), pi's matching (exact, then tolerant of trailing whitespace, Unicode compatibility forms, quotes, dashes and spaces, every untouched line's bytes kept), CRLF and BOM kept, pi's error texts; the model reads "Successfully replaced N block(s) in path.", the diff is kept for people | done (phase 1, loop parity B) |
| T2 | bash: stdout and stderr as one stream in the order written; the last 2000 lines / 50 KB shown, the whole kept in `.githi/output/` and named ("[Showing lines a-b of N. Full output: …]"); "(no output)"; a non-zero exit ends "Command exited with code N" and is flagged as an error, a timeout "Command timed out after N seconds" | done (phase 5, loop parity B) |
| T3 | read: 2000 lines / 50 KB per call with pi's notes ("Showing lines a-b of N (50.0KB limit). Use offset=…"), a line longer than a page pointed to bash rather than cut, pi's offset error; binary and images reported | done (phase 1, loop parity B) |
| T4 | grep (`path:line: text`, context lines, `literal`, `ignoreCase`, `limit`), find (new: glob, directories marked), ls (dotfiles listed, `limit`): pi's arguments, results and notes ("100 matches limit reached. Use limit=200 for more, or refine pattern"), `.gitignore` respected; write: "Successfully wrote to path" | done (loop parity B) |
| T5 | Tool results reach the model as text; a failure is its message (pi's), flagged to the provider; bad arguments are pi's "Validation failed for tool …" with the arguments received; an optional argument sent as null is one not sent | done (phase 1, loop parity B) |
| T6 | Tool descriptions and parameters as pi's: the registry publishes them (so agents published from the builder get them), and the shipped tool sets `githi-tools` / `githi-coding-tools` 1.3.0 carry them, with find; calls in the older tool sets' argument names still parse | done (phase 1, loop parity B) |
| T7 | websearch, webfetch, image, ask_user | later (each needs a backend or UI contract) |

### Model layer, memory, subagents

| # | Capability | Status |
|---|---|---|
| M1 | Prompt caching, cache-token usage, thinking levels, reasoning replay | same (in `models/` + `gateway/`) |
| M2 | Long-term memory | better (memory service, notebooks, recall, history tool) |
| M3 | Subagents: foreground, background, result | same |
| M4 | Fork-with-context and continuable children as model tools | later |
