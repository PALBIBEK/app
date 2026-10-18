# Subagent contract (normative)

**QUEUE:** `subagent-contract`  
**Placement:** contracts + `services/supervisor/src/supervisor/session/{tree,subagent_ops}.py`  
**Seam:** `SubagentTransport` ([`seams.md`](./seams.md))

## Principle

Supervisor observes children via **journal events** and **commands**, never by
importing harness/Cordis child types into `session/` / `journal/` / `api/`.

## Events

Wire types (snake_case): `subagent_start`, `subagent_end`, `job_start`, `job_end`.

Python payloads: `SubagentStart`, `SubagentEnd`, `JobStart`, `JobEnd` in
`supervisor.agent.events`. Pair on `run_id` / `job_id`.

## Session tree

`build_session_tree(parent_session_id, envelopes)` folds journal events into
`ChildRow` / `JobRow` lists. Unpaired `subagent_start` ⇒ unknown outcome
(same recovery signal as unpaired `tool_start`).

## Ops bridge

`SubagentOps` (session package):

- Accepts `SubagentTransport` Protocol + `JournalWriter`
- Journals `SubagentStart` **before** provider work for spawn/fork
- Journals `SubagentEnd` on success or error
- Background → `JobStart` + `SubagentStart` (`mode=background`)

Factory / binders construct providers; ops never imports `seams.providers.*`.

## Commands

Documented in [`AGENT_CONTRACT.md`](./AGENT_CONTRACT.md) (recommended). MVP
implements ops methods; HTTP route surface may lag.

## Out of scope here

Full ACP continuable session product; per-tool catalog `provider_ref: mcp` rows.
