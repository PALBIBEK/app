# Supervisor experiment report — 2026-10-21

**Status: every problem below is fixed on `feat/githi-parity`** (see "After the fixes" at
the end for the re-run). The findings are kept as found, for the record.

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

## After the fixes (same day, same scripts)

| # | Fix | Re-run evidence |
|---|---|---|
| 1 | The stall guard counts calls that get nowhere: a successful call with new arguments resets it (patched for replay) | 11-step task: 11 tool calls, no `LoopGuard`, full answer |
| 2 | Step limit from the manifest's `loop:` (default 30); "N steps left" on the last requests; the last step sends `tool_choice=none` | same run: 12 model steps, `stop`; scripted test: warned, then made to answer on step 30 |
| 3 | `brain()` syncs pins before resolving them | regression test reproduces the live `NotOffered`, passes after |
| 4 | Recall skipped with a notice to the model; memory tool errors are readable; a session without named notebooks starts without memory | memory service stopped: create 201 in 0.2 s, turns run |
| 5 | Production/staging refuse host sandboxes at boot and per profile, and never fall back to them (`ALLOW_HOST_SANDBOX=1` to opt in) | unit tests on the real boot path |
| 6 | No route override from a provider key; explicit signals still upgrade `echo`, and every override is logged | 30 users × 3 echo turns: 0 errors (they all failed before) |
| 7 | Command output clipped to 12k characters a stream, keeping start and end, with how to see the rest | `seq 1 300000`: 14,584 characters (was 76,650), next prompt 8.6k tokens (was 39k), right answer in 2 steps |
| 8 | `edit` (and `bash` with `timeout`) in the shipped tool sets | bug fix done with `edit`; other 60 functions intact |
| 9 | `command_id` ≤ 200 chars; NUL refused in text, ids and URLs; validation errors stop echoing the body | all hostile inputs answer 4xx |
| 10 | (with 9) NUL no longer reaches the events table | — |
| 11 | `queued` on message responses; `duration_ms` set; a direct invoke journals the same result shape (it dropped non-dict payloads to `{}`) | journal |

Found while fixing, and fixed too:

- **Changing a shipped artifact orphaned everything pinned to the old version** (and failed
  readiness for the whole API). Shipped artifacts are now recorded in the database at boot,
  the replaced versions ship frozen in `harness/previous/`, and readiness considers only
  shipped compositions.
- **`import supervisor.tools` first failed with a circular import** (already on `main`); fixed,
  with a test that imports every entry point first.
- **Truncated or malformed tool calls ran** with clipped or empty arguments (from the githi
  parity check): now refused with a message telling the model to resend.

Verification: 1,096 tests pass (SQLite and Postgres), ruff and mypy clean, e2e stages 3, 4, 6,
7 and 7q pass; stage 2's one Docker check needs Docker Hub, which this environment blocks
(CI's runners have it).

## Rating after the fixes

| Dimension | Before | After |
|---|---|---|
| Security boundaries (API) | 9 | 9 |
| Reliability and durability | 8 | 9 |
| Agent usefulness | 4 | 7.5 |
| Operability and safety defaults | 6 | 8 |
| **Overall** | **6.5** | **8.5** |

What keeps it from higher: Docker and e2b isolation are unverified here; no measurement on real
hardware under load; mid-turn compaction is still missing (a long tool-heavy turn on a small
window still hits `ContextWindowFull`); one free model tested live.
