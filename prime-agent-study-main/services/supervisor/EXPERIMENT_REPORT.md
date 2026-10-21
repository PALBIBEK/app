# Supervisor experiment report — 2026-10-21

The full stack (API, two workers, gateway, memory service, Temporal, Postgres, S3)
was exercised as a user and as an operator would, beyond what CI covers. Real-model
runs used the **shipped** githi configuration (prompt pack `coding-prompt`, tool set
`githi-coding-tools`) on `minimax/minimax-m3.1-flash:free` via OpenRouter, $0 spent,
about 45 model calls (the free tier allows 50 a day). System runs used the
gateway's `echo` route.

Not testable here: Docker and e2b sandboxes (Docker Hub is blocked from this
environment; CI's compose job covers Docker). Bash ran in the `local_process` sandbox.

Scripts: `user_scenarios.py` and `system_scenarios.py` (kept outside the repo; ask to add them under `e2e/`).

## Results

### Works (with evidence)

| Area | What was tried | Result |
|---|---|---|
| Authentication | no token, garbage, wrong secret, expired, wrong audience, `alg: none`, user (not service) token | all 401 |
| Tenant isolation | another tenant reads, streams, messages, invokes, forks, aborts, deletes a session | all 404 (no existence leak); same-tenant other user also refused; artifact names owned per tenant |
| Duplicates | one command sent 8 times concurrently | one run, every reply 2xx |
| Event stream | 3 turns, replay, `Last-Event-ID`, 60 concurrent readers | gapless seqs, exact resume, all readers complete |
| Lifecycle | delete twice, then message / invoke | 204, 204, then 409 |
| User secrets | put, list, other tenant, journal | value never listed, never in the journal, invisible to other tenants |
| Shell environment | `env` in the agent's shell | no platform secrets (scrubbed) |
| Load | 30 users × 3 turns concurrently | 0 errors (latency limited by a 2-CPU box: p50 8.8 s vs 2.1 s for one user) |
| Chaos | API restarted mid-tool; Postgres restarted; gateway stopped and restarted | tool completed; turns resumed within seconds; gateway outage reported as an error, session usable after |
| One process | `python -m supervisor.main` with all three parts | one PID serves :8080, :8090, :8092, all ready |
| Real model | tools/working-directory awareness, 20k-line log questions (answered with grep/sed, 3.8k-token prompts), Hindi input, abort and recovery, a message queued while busy, a broken model route | all correct; the broken route's error names the cause without leaking the key |

### Problems found

| # | Severity | Problem | Evidence |
|---|---|---|---|
| 1 | **High** | **The loop guard ends productive runs.** In stepped turns the guard is never reset inside a run, so "stall" means 10 tool calls in one run, whatever they achieved. | Coding task: the model installed pytest, all 6 tests passed, then `LoopGuard: stall` killed the run; the user got an empty reply. |
| 2 | **High** | **8 model steps, hard-coded, then an error with no answer.** No warning near the limit, no final answer. | 11-step task: `AgentLoopError: no answer after 8 model steps`, empty reply. |
| 3 | **High** — fixed on `feat/githi-parity` | **Model steps failed with `NotOffered` on a worker that had not synced the catalog.** `brain()` resolved the sandbox profile before `sync_composition`. Intermittent: depended on which worker took the step. | Reproduced live with two workers; regression test `test_a_worker_thinks_for_a_session_whose_sandbox_profile_it_has_never_seen` fails before, passes after. |
| 4 | **High** | **A memory-service outage stops every conversation.** New sessions: 503 after 15 s. Existing sessions: turns fail with `MemoryUnavailable`. Recall should be skipped, not fatal. (Introduced by moving notebooks into the memory service.) | Memory service stopped during a live session. |
| 5 | **High** (config) | **Production does not force tenant isolation.** `MULTI_TENANT_STRICT` defaults to false in every profile; if the Docker/e2b library is missing, the supervisor logs a warning and runs every tenant's bash on the host. | Shown on `local_process`: the agent's shell read `SERVICE_JWT_SECRET` and `DATABASE_URL` from `/proc`, found another tenant's file, wrote to `/usr/local/bin`, and `pip install`ed into the host as root. |
| 6 | Medium | **A pinned model can be silently replaced.** `apply_runtime_model_route` turns a composition pinned to `echo` into the `githi` route (Claude Sonnet) when a provider key is in the environment. | 312 turns pinned to `echo` went to OpenRouter as Sonnet and failed. |
| 7 | Medium | **Tool output is unbounded for the model.** One `bash` call sent 76,650 characters; the next prompt jumped from 2.4k to 39k tokens. | `seq 1 300000`. |
| 8 | Medium | **The shipped githi tool set has no `edit` tool.** Fixes are done by rewriting files or with `sed`. | Bug-fix task used `sed -i`. |
| 9 | Medium | **Three inputs give 500 instead of 4xx.** `command_id` over Temporal's update-id limit; deeply nested JSON (`RecursionError` while rendering the error); a NUL byte in a session id (Postgres encoding error). | API log tracebacks. |
| 10 | Low | A NUL byte in a message is stored in the journal as `\u0000`, which breaks SQL JSON operators on `events` (analytics, a future `jsonb`). The supervisor itself decodes in Python and is unaffected. | `psql` query over `events`. |
| 11 | Low | The API does not say a message was queued behind a running turn (`status: accepted` either way). `ToolEnd.duration_ms` is never set. A direct invoke and a turn journal tool results in different shapes. | Journal. |

## Rating

| Dimension | Score | Why |
|---|---|---|
| Security boundaries (API) | 9/10 | Every forged token and cross-tenant attempt refused with no existence leak; secrets kept out of listings, the journal and the shell environment. |
| Reliability and durability | 8/10 | API, Postgres, gateway and worker restarts all recovered; duplicates run once; but a memory-service outage halts everything (4). |
| Agent usefulness (the product a user meets) | 4/10 | Answers and short tasks are good, but 2 of 3 real multi-step tasks ended with no answer because of the platform's own limits (1, 2). |
| Operability and safety defaults | 6/10 | Silent fallbacks (5, 6) turn configuration mistakes into isolation or model changes. |
| **Overall** | **6.5/10** | Strong platform, weak agent loop defaults. Fixing 1, 2, 4, 5 and 6 — each small — would put it near 8.5. |
