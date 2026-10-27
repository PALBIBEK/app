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
| L1–L3 | Step limit from the pins (default 30), "N steps left" on the last requests, a final step with no tools | same |
| L4–L5 | A cut-off reply's calls and malformed arguments are refused, not run | same |
| L6 | Every tool call answered | same |
| L7 | Compaction before any model call that needs it (`COMPACT` activity, keyed by step), and recovery from a provider's context overflow: compact once, ask again, else end the run saying why. The run's request is kept verbatim beside the summary, and the tool round the model has not seen yet is kept whole when it fits | done (phase 3); background compaction later |
| L8 | Durable execution: per-step journal, adoption, at-most-once tools | better |
| L9 | Loop guard, durable approvals, cost budget | better |
| L10 | Abort | same |
| L11 | Steering a running run; follow-ups after it | next (phase 6) |
| L12 | Streaming text, thinking and tool output | next (phase 6) |
| L13 | The loop as explicit phases over a checkpoint | done (phase 2) |
| L14 | Retryable model errors back off with durable sleeps; crash loops are parked | next (phase 4) |
| L15 | Parallel tool rounds; per-tool replay policy | next (phase 5) |

### Prompt

| # | Capability | Status |
|---|---|---|
| P1 | pi's structure: preamble (the pinned pack), `<tools>`, `<rules>`, `<cwd>`; stable, so the provider's cache hits | done (phase 1) |
| P2 | Skills listed in the prompt | later (needs a skills artifact kind in the catalog) |

### Tools

| # | Tool | Status |
|---|---|---|
| T1 | edit: batch edits, CRLF and BOM kept, curly quotes matched, diff returned | done (phase 1) |
| T2 | bash: output capped; the full output saved to a file it names | next (phase 5) |
| T3 | read: 2000 lines / 50 KB per call with the offset to continue; binary and images reported | done (phase 1) |
| T4 | grep, ls, write | same |
| T5 | Tool results reach the model as text, failures stated plainly | done (phase 1) |
| T6 | Tool descriptions as pi's | done (phase 1) |
| T7 | websearch, webfetch, image, ask_user | later (each needs a backend or UI contract) |

### Model layer, memory, subagents

| # | Capability | Status |
|---|---|---|
| M1 | Prompt caching, cache-token usage, thinking levels, reasoning replay | same (in `models/` + `gateway/`) |
| M2 | Long-term memory | better (memory service, notebooks, recall, history tool) |
| M3 | Subagents: foreground, background, result | same |
| M4 | Fork-with-context and continuable children as model tools | later |
