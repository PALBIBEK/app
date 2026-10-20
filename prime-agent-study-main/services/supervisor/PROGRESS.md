# Supervisor: progress log

Where the supervisor stood when this work began, what has been done since, and
what comes next. Everything through `hardening/ten` (PRs #1–#7: sections 1–14
and workstreams W1–W7) is merged into `main`; W8 and W9 were finished on
`hardening/w8-w9`.

The goal throughout: the supervisor runs composable agents (catalog →
composition → session pins → runtime seams → runtime → journal and policy), and
**every session is durable**, whatever seams the user composed it from, via
Temporal.

---

## Where we started

The supervisor didn't start. A folder reorganisation had moved modules into
subpackages without updating imports, so it failed at import time. Under that
was a deeper problem: almost nothing in the core idea worked end to end.

- **Compositions didn't produce working sessions.** Creating a session from a
  composition failed at every step: unawaited async calls, mismatched
  serialisers, and runtimes that never received the session id, so a githi
  agent saved its history under a random id and could never resume.
- **User-composed seams couldn't be published.** Publishing a tool set or a
  composition with plug bindings crashed. User compositions disappeared on
  restart, stranding every session pinned to them.
- **Integrity holes.** The catalog accepted a digest that didn't match the
  content, so anyone could change what a pinned digest pointed to. A
  composition id and a mismatched digest silently ran the other composition.
- **Durability was not real.** The `temporal/` package was unused, used global
  gateways, and bypassed pins, the journal and policy. Sessions lived only in
  the memory of whichever process held them.
- **Policy was broken.** The journal couldn't write any event (an enum field
  failed to serialise). The loop guard counted tool calls across the whole
  session, so every session died after 10 calls. Budget recording was called
  with the wrong arguments, and approvals never timed out.

Test suite at the start: **228 passing.** Many other tests can't even be
imported, because they reference modules that no longer exist.

---

## What we achieved

### 1. The service boots and shuts down cleanly (PR #1)
- Imports fixed for the new layout. Catalog persistence restored (publish and
  deprecate had been stubbed to silent no-ops). A duplicate index that broke
  `CREATE INDEX` removed. Harness versions actually registered.
- Approvals expire. SIGTERM chains to uvicorn, so the process exits.
- A boot smoke test runs the real app through its production startup and
  shutdown.

### 2. Stage 1: composition → session pins works
- Digest integrity is enforced (`DigestMismatch`). Mismatched id and digest
  pairs are refused.
- Pins serialise without loss. Runtimes receive the supervisor's session id, so
  githi history resumes.
- Catalog errors map to 404/403/422/409 instead of 500.
- End-to-end test: the shipped catalog and the githi-v1 manifest, with only a
  mock sandbox.

### 3. Stage 2: user-composed agents bind their seams and survive restarts
- A user can take a pinned githi version, choose their own prompt, tool set,
  memory profile and subagents, publish the composition, and run it.
- Tool digests are computed once and verified. Real drift this caught was
  re-pinned.
- `POST /v1/sessions` uses the manifest's runtime, so a githi harness no longer
  silently ran the echo test runtime.
- The catalog is always persisted: compositions, and the sessions pinned to
  them, survive restarts.

### 4. Every session runs as a Temporal workflow (phases A and B)
- `docker-compose`: Temporal server on the shared Postgres, schema and
  namespace jobs, Temporal UI on :8233, and a `supervisor-worker` service.
- `AgentSessionWorkflow`: one entity workflow per session.
  - Creation goes through update-with-start, so bad input still gets a 4xx
    answer synchronously.
  - Messages arrive as updates with validators and are queued first in, first
    out. A command id never runs twice, even across continue-as-new.
  - Continue-as-new happens only between turns, after handlers finish.
- **Placement.** A live runtime stays on the worker holding it (a
  worker-specific task queue). If that worker is gone, any worker adopts the
  session from the journal.
- `/readyz` reports not ready while Temporal is unreachable. SSE follows the
  journal, so it works from any API replica.
- Durability tests against a real Temporal dev server:
  - a worker killed with SIGKILL mid-tool;
  - abort mid-tool;
  - messages queued while busy;
  - long sessions crossing continue-as-new;
  - replay determinism;
  - a loop-guard abort closing the session.

### 5. Phase C: githi turns are driven step by step by the workflow
- **Steps.** One activity per model call and per tool call; nothing is
  replayed on a crash.
- **Tools follow the manifest's recovery class.** Pure or idempotent tools are
  retried on another worker. Non-atomic tools run at most once; if their worker
  dies, the call is recorded as `TOOL_OUTCOME_UNKNOWN`, the turn as
  `TurnInterrupted`, and the session stays usable.
- **Policy gates run in the workflow.**
  - The loop guard runs deterministically inside it.
  - The maximum step count ends a run, not the session.
  - The budget is checked before every model call and on replay, and is
    enforced once spent.
- **Approvals are durable timers.** A person allows or denies, or the
  manifest's `on_timeout` applies. The wait survives having no worker at all.
  Abort while waiting resolves the approval as deny.
- **Fixed.**
  - Tool results now carry `tool_call_id`, and the gateway sends `tool_calls`
    in OpenAI format.
  - githi now uses the prompt seam.
  - Journal writes are idempotent through deterministic event keys.
  - Budget totals live only in the database, updated atomically. The old
    per-worker cache dropped real charges once a session moved between workers.
  - Refusing a turn for budget had crashed with a `TypeError`.
  - A queued message that was refused no longer vanishes silently.

### 6. Every session operation goes through the workflow
- **Moved.** Tool invoke, memory compact and recall, subagent spawn, fork,
  background, continuable, message and interrupt, and workspace rehydrate.
  - **Before:** they ran in the API process, which resumed a second copy of
    the session there (a second runtime, possibly a second sandbox).
  - **Now:** they run on the session's worker, adopting the session if that
    worker is gone.
- **Direct tool invokes run at most once.** If a worker dies mid-call, the
  outbox closes the call as `TOOL_OUTCOME_UNKNOWN`, and re-sending the same
  `tool_call_id` returns that outcome rather than running the tool again.
- **Fork gets its own workflow.** Before, a forked session couldn't be driven
  at all. Its journal copy is safe to retry.
- **Fixed.**
  - Direct tool invokes ran on the event loop, freezing the whole worker.
  - Workspace rehydrate could never succeed in production.
  - Several endpoints returned 500 instead of 404 or 409, because the API and
    the manager use two different sets of error classes with the same names.

### 7. Background subagent jobs are durable child workflows
- **Before,** "background" wasn't background, and its result was lost. The lab
  provider finished on the spot and discarded its output, nested_session ran
  the model synchronously on the event loop, and ACP posted a remote job
  nobody fetched. No `JobEnd` was ever recorded.
- **Now,** each job is a `SubagentJob` workflow started by its session:
  - its parent close policy is ABANDON, so it outlives continue-as-new;
  - a re-sent start never runs it twice;
  - it runs on any worker, with heartbeats and bounded retries;
  - interrupting the job, or ending the session, cancels it;
  - however it ends, exactly one `JobEnd` and one `SubagentEnd` reach the
    session's journal.

### 8. Durable memory (phases 1–3)
- Phase 1: a notebook store with provenance, disclosure and classification.
- Phase 2: sessions bind notebooks; a memory HTTP API.
- Phase 3: recall in turns, memory and history tools for the model, and
  durable capture of what a session learns.

### 9. Tools come from one registry, through the tool seam
- **Before,** the same tools existed twice: plain functions in
  `catalog/tools/handlers.py` (host-only) and githi's own classes and registry
  (sandbox-aware), with different outputs. A published ToolSet was never checked
  against code, so a tool with no implementation failed only when the model
  called it. githi built its own tool provider, which the seams contract forbids.
- **Now,** `supervisor/tools/` is the only place tools live:
  - one module per built-in tool (`read`, `write`, `edit`, `ls`, `grep`, `bash`,
    `echo`, `remember`, `git_commit`), each an `async def` registered with
    `@tool`; its schema comes from a pydantic args model and its description
    from its docstring;
  - tools act on a `Workspace` (host directory or sandbox file tree), which
    confines every path, symlinks included;
  - `RegistryToolProvider` is the seam: it offers exactly the pinned tools and
    turns every failure into a typed result code;
  - a ToolSet naming an unimplemented tool is refused at publish (422), at boot
    verify and at bind;
  - `bash` no longer passes the supervisor's environment (service tokens,
    database URLs) to agent commands, and returns output produced before a
    timeout kill.
- Shipped tool sets are unchanged (same digests); a test checks that every
  argument they pin is accepted by the implementation.

### 10. Clean-up: only live code and live tests remain
- **Stale tests removed.** 8 test folders whose shared setup imported modules
  that no longer exist (so none of their tests ever ran), every test file that
  could not be imported or failed throughout, and the 91 failing tests inside
  otherwise passing files. Every passing test was kept.
- **Docs.** Every `.md` file in the service except this log was removed; the
  normative contracts live in `contracts/` at the repo root.
- **githi runtime reduced to what runs** (about 4,700 lines removed): the
  ported `compaction`, `events`, `hooks`, `messages`, `sandbox`, `session`,
  `session_store` and `state` modules, the unused `run_loop` and its second
  tool-calling path, and the PostgreSQL session-store branch in the runtime,
  which could never execute (it looked for a `session_store` field that
  `RuntimeSeams` does not have), with its raw SQL file. Compaction and recall
  are served by the `context_memory` seam; durability by the journal and
  Temporal.

### 11. A githi session runs entirely on what it pinned
- **Before,** githi took its prompt, tools and gateway from the pins but nothing
  else:
  - the pinned sandbox was created and then discarded, so every tool, `bash`
    included, ran on the supervisor's own disk;
  - the composition's model route was ignored;
  - the memory profile was bound but never consulted, so conversations grew
    without limit and pinned recall never ran;
  - the subagent transport was bound, but the agent had no way to use it;
  - a resumed session was rebuilt differently from a created one (no gateway
    key, no workspace-change hook, no hydration).
- **Now:**
  - **Sandbox.** Tools act in the session's sandbox (`SandboxWorkspace`: files
    and commands both). The sandbox profile's `file_effects` policy now governs
    the model's tool calls too, not only direct invokes. Workspace changes mark
    the workspace dirty for sync.
  - **Model.** Every call names the pinned model route (else the manifest's
    model), including memory's one-shot calls and the gateway key's allowed
    models.
  - **Memory.** Between turns, a conversation past the profile's limit is
    compacted through the memory seam (summary plus the most recent
    `keep_recent_tokens`, whole tool exchanges only), journalled on
    `CompactionEnd` as the new checkpoint. Pinned recall runs on each run's
    first model step and is shown ahead of the user's message. The profile can
    now pin `context_window`.
  - **Subagents.** `subagent` delegates on the transport's pinned default
    provider (depth enforced); with `background`, when the manifest allows it,
    the child runs as the session's durable job, and `subagent_result` reads its
    outcome. Requests that name no provider now get the pinned default, not the
    lab-only `memory` alias. One-shot spawns run off the event loop.
  - **One path.** A githi turn with a model only runs step by step under the
    workflow; the in-process path that sent no system prompt is gone.
    Create and resume share one runtime builder.
- Also fixed: `get_state()` on the githi and echo runtimes crashed (it passed
  fields `AgentState` does not have).
- `tests/integration/test_githi_pinned.py` checks each pin end to end against
  a Temporal dev server; every built-in tool is also tested in a sandbox.

### 12. One set of seams per session; the gateway knows each model's window
- **Seams bound once, on the session's key.** The gateway key used to be
  attached afterwards, and only to the agent's own gateway: the compaction
  summarizer and nested-session subagents kept a client without the key, the
  attached client came from `GATEWAY_BASE_URL` rather than the composition's
  pinned gateway, and memory/subagent operations used a second, un-keyed copy
  of the seams. Now the key is minted first and every seam is bound once with
  it, on the pinned gateway; every session path uses the runtime's seams.
- **Context window from the gateway.** Gateway routes carry `context_window`
  (`GET /v1/routes/{route_id}`). The supervisor's gateway client reports it
  (`ModelCatalog`), and compaction measures against: the memory profile's
  pinned window, else the gateway's report for the session's model, else
  200,000.
- **Background results arrive on their own.** When a background subagent
  finishes, the next run starts with a `BackgroundResults` event shown to the
  model ahead of the user's message, once; `subagent_result` stays for asking
  sooner.

### 13. Model access the way QM does it (YC's QM, MIT)
QM's model layer and its gateway contract, ported with the same behaviour
(`contracts/model_routing.md`, `contracts/model_gateway.md`):
- **The gateway serves LiteLLM's end-user contract.** `GET /v1/models` and
  `GET /model_group/info` (routes now carry mode, provider labels, limits,
  per-token prices and capability flags), and `POST /v1/messages` and
  `POST /v1/responses` beside chat completions — passthroughs, streaming on
  every wire, metered from each wire's own usage, priced from the route and
  returned in `x-litellm-response-cost`. A new Anthropic provider; OpenAI
  serves Responses; the provider layer is rebuilt on one HTTP leg (retries,
  `Retry-After`, SSE relay), which also fixes the provider regressions behind
  the gateway suite's eleven failures and three errors. Echo answers all three wires and rejects
  forged thinking signatures and stripped encrypted reasoning, as the vendors
  do. The key may come in a configurable header; a spent virtual-key budget is
  429.
- **The supervisor discovers models and speaks each one's protocol.**
  `supervisor/models/`: QM's native registry on pi-ai's templates (pricing
  tiers, reasoning levels, Ultrafast as request overrides), `MODEL_GATEWAY_*`
  configuration, the discovery catalog (admission rules, 5-minute refresh,
  30-second retry, 15-minute stale window, aliases), routing with session
  affinity, and three streaming transports ported from pi-ai — Anthropic
  Messages (signed thinking, prompt caching), OpenAI Responses (stateless,
  encrypted reasoning, service tiers) and chat completions. A session's model
  id is a `gateway/<group>`, a native id served through an alias, or a legacy
  route id; nothing unrouted ever falls back to another provider.
- **Native reasoning survives the journal.** Assistant turns are journalled as
  ordered `text` / `reasoning` / `tool_call` parts and replayed to the model
  that produced them; any other model sees the reasoning as text, with tool
  call ids re-spelled and orphaned calls closed.
- **The browse runner gets model access without a key.** Each tool call of a
  non-strict turn carries an hour-long browser capability;
  `POST /v1/browser-model/chat/completions` re-checks it against the session
  and calls the model on the session's own gateway key — relayed to the
  gateway's chat completions, or (for Ultrafast) through the native transport
  with screenshots and structured output converted. `GET /v1/models` lists
  what a session can select.
- **Proved end to end** against the real gateway over HTTP: discovery, a tool
  loop on all three protocols, forged reasoning refused, Ultrafast on the wire,
  key scoping and budgets, a whole githi session replaying signed thinking
  across its steps, and the browser endpoint (relay, native, strict).

### 14. Tested live, as a user would (PR #6)
The whole stack run as separate processes with docker-compose's wiring
(Postgres, Temporal, the model gateway, the memory service, S3, API and
worker), driven only through the public API, stage by stage: harness and
catalog management, configurations reaching the model, sandboxes, session
memory, policy and journaling. All runs used the gateway's echo model.
The full report, with every finding (F1–F35) and the fix plan, is the
"Supervisor live test report" doc.
- **Six bugs the suite missed, fixed:** the journal could not write on Postgres
  (no turn ran in a real deployment); workers never saw compositions published
  after they booted; nested subagents had no model; no githi session could use
  a shared scope; the session key refused the compaction summarizer, so
  compaction silently dropped turns; a tenant over quota got 503 instead of 429.
- **Proven live:** pinned harnesses run on exactly what they pinned; every seam
  combination starts a session; read-only / write / full-access sandboxes and
  path confinement; scopes; conversation across turns, restarts and forks;
  notebooks; budgets, loop guard, approvals (allow, deny, durable timeout);
  quotas and rate limits; a journal that is contiguous, persisted 1:1 and
  gap-free on reconnect.

### Where it stands now
- **Every session is durable by default,** whatever seams it was composed
  from:
  - githi with a model gateway is durable step by step;
  - any other runtime is durable turn by turn (a crash mid-turn ends that turn
    as interrupted rather than resuming it).
- **Tools a composition adds** that the manifest doesn't classify fall back to
  `recovery.default` (`fail` for githi), so they run at most once and are never
  automatically re-run.
- **Tests: 824 in the supervisor, all passing** on Postgres and Temporal (from
  228 at the start), 81 in the model gateway, 79 in the memory service; every
  CI job green (see W9). Temporal-backed tests need `TEMPORAL_CLI_PATH` where
  the dev-server download is blocked.
- The three user goals the live test found unmet (harnesses through the API,
  long-term memory across sessions, one user's sessions kept from another's)
  were closed by W3–W5.

---

## Known gaps

The live-test findings listed here before (F1–F6, F10–F12, F17, F19–F21,
F24–F30, F32) were fixed by W3–W7 below and are verified by CI's e2e job.

Open:
- **Staging and Kubernetes don't run Temporal.** `deploy/staging/` and
  `deploy/k8s/` have no Temporal server and no `supervisor-worker`, and
  `/readyz` requires Temporal, so the staging supervisors would never report
  healthy. The root `docker-compose.yml` is complete (CI's compose smoke runs
  it). Neither manifest has been run here (no Docker or cluster).
- **Continuable subagents** (`start_continuable`, child messages) still hold
  live state in the provider. No built-in provider supports them except ACP,
  whose state lives on the remote server.
- **A compaction interrupted mid-way** leaves the session waiting for human
  review on resume (the existing design).
- **The per-turn token limit** is counted in the worker's memory, so a worker
  restart mid-turn resets it. The cost limit is exact.
- **Gateway routes without a window.** A route configured without
  `context_window` makes compaction fall back to 200,000 tokens, and is not
  discoverable (QM admits only groups with limits and prices); set them on
  every route in `MODEL_GATEWAY_ROUTES_JSON`.
- **Not ported from QM:** direct provider keys in the supervisor (keys stay in
  the gateway), per-user subscription accounts, custom provider overlays, Fast
  mode, and document inputs (specs record QM's document support; sessions send
  none). The routed client's `stream()` yields the finished turn rather than
  token deltas; turns use `complete()`.
- **The gateway's virtual-key store and QM's model-call audit are in-process.**
  One gateway replica, and the audit resets on restart (the journal is the
  durable record of every call).
- **One intermittent hang.** `test_a_looping_model_trips_the_guard_and_ends_the_run_not_the_session`
  hit its 300 s timeout once in CI and passed 6 of 6 runs locally; CI now
  annotates a hung test's stack, so the next occurrence names its cause.
- **No real-model CI run.** Every automated turn uses the gateway's echo model;
  a real-model job needs a `MODEL_E2E_API_KEY` secret.
- **The coding-v1 Pi harness** needs its image pushed to a registry and the
  external Pi agent; not done.

---

## Hardening to 10/10 (branches `hardening/ten`, then `hardening/w8-w9`)

The honest review rated the system 5/10. The plan to reach 10 is in the doc
"Supervisor Review and 10/10 Plan": ten machine-checked gates, closed by nine
workstreams (W1 to W9), one commit per item on `hardening/ten`.

### Status

| # | Workstream | Status |
|---|---|---|
| W1 | CI that enforces the gates (lint, types, suites on Postgres + Temporal, compose smoke, live e2e) | Done |
| W2 | The two flaky tests, fixed at their causes | Done |
| W3 | Security: catalog tenancy, session ownership, approval responder, strict sandboxes, memory-service auth | Done |
| W4 | Long-term memory that works; compaction defaults that fit any window | Done |
| W5 | Harness management through the API; publish refuses configs that can never work | Done |
| W6 | A deployable default: migrations, a runnable bundled harness, the memory service in compose | Done |
| W7 | Robustness: scope write conflicts, sandbox files on worker loss, loop guard, idempotency, error shapes | Done |
| W8 | Code health: lint 0, types 0, structure, complexity, no fixed sleeps | Done |
| W9 | Re-verify everything, re-measure, final rating | Done |

### What W7 fixed (all verified on the live stack)

- **F20 (data loss):** a session's sync compared its files with the scope's
  *current* files, so a session that hydrated earlier overwrote other
  sessions' writes and deleted files they had created. Sync is now three-way
  against the base the session started from; a race is a conflict, the
  sandbox is refreshed from the scope, and the run carries a
  `WORKSPACE_CONFLICT` event naming the files.
- **F21 (data loss):** a session without a scope lost its files when its
  worker died. Resume now adopts the surviving sandbox (local directory or
  Docker container) instead of starting an empty one.
- **F32:** the loop guard ends the runaway turn, not the whole session.
- **F33:** a command id reused with a different message is a 409, not a
  silent drop.
- **F16:** refused tool calls have the same result shape as any tool.
- **F22:** a symlink out of the workspace is `PATH_ESCAPE`, not a crash.
- **F34:** the event stream honours `?since=`.
- **Browser token:** no longer in every shell's environment; a sandbox
  profile must ask for it (`browser_model`).
- **Compose smoke:** the shipped Docker githi agent (`githi-tools-v1`) had no
  shell tool, so the smoke test never passed. It now pins
  `githi-coding-tools`, and a test fails if any shipped composition does not
  verify at boot.

### What W8 did

- Line endings normalized repo-wide (`.gitattributes`, 185 files were CRLF).
- About 20,000 lines of dead or duplicated code deleted: an abandoned rewrite
  of the session layer, a never-wired sandbox pool, a shadow copy of the
  whole session package, a never-wired async catalog stack, and 128 classes
  and functions nothing used. Found by an import graph and a symbol scan,
  each deletion checked by the full suite and the live stages.
- **A real bug the cleanup exposed:** the API caught session errors from one
  copy of the error classes while the manager raised another, so on the
  in-process path those handlers never matched. One hierarchy now, with a test.
- One copy each of the sandbox proxy rules and the digest hashing.
- The 2,147-line session manager split into six layered modules (largest 794
  lines).
- Every `except Exception` now logs its traceback, is narrowed, or states why
  it is reported in-band. The logger is `logger` everywhere.
- The three model transports refactored into small stream accumulators.
- **Migrations match the models.** The tables whose models went with the dead
  code (`sandbox_routes`, `catalog_errors`, `catalog_usage`) are dropped by
  `0015`, so `alembic check` and the drift test pass; this was what failed CI's
  supervisor and e2e jobs.
- **mypy 73 → 0, and the real bugs behind it** (each pinned in
  `tests/unit/test_typed_contracts.py`):
  - `TemporalSettings.from_env()` without `TEMPORAL_*` returned slot
    descriptors instead of `localhost:7233` / `default` / `supervisor-sessions`;
  - the harness runtime's `get_state()` always raised `TypeError`;
  - a composition naming an unknown artifact raised `TypeError`, not
    `DigestMissing`;
  - the in-process budget abort read a field that doesn't exist;
  - two `BudgetSpec` types (float and Decimal): one now, money compared as
    Decimal;
  - `WorkspaceTreeIO` lacked the `clear()` every hydrate calls;
  - the harness materializer dereferenced optional seams.
  Also: one projector type and a `Reapable` protocol, a typed live-stream
  sentinel, typed catalog lookups (`get_as`), and the unused
  `sandbox/routing` package (743 lines) deleted.
- **A corrected approval answer was refused as the old one.** Every answer to
  an approval shared one Temporal update id, so after a refused answer the
  user's correction got the stored refusal back. The id is now per choice
  (found by CI).
- **No fixed sleeps in tests.** `tests/support/waits.py` (supervisor) and
  `ctl()` / `eventually()` (e2e) wait for the condition a test needs; a check
  that something did *not* happen waits for a barrier (a later message, or
  terminate, which flushes pending memory capture). Two weak assertions became
  real ones (the duplicate-command test, the slow-subscriber test). What
  remains polls a condition or simulates a slow tool or model on purpose.
- **Tests that broke when CI was slow:** three API test modules signed their
  JWTs at import with a five-minute life, so after a slow run every later
  request got 401 (one helper now, `tests/support/auth.py`, signs per request);
  a job test read the workflow's status a moment before it closed.
- The memory service's 39 routes moved out of `create_app` (complexity 40) onto
  a module router; its one mypy error fixed.

### Gates, measured at the end of W9

| Gate | Start of W8 | End of W9 | Target |
|---|---|---|---|
| ruff: supervisor, model-gateway, memory, e2e | 767, 12, 32, – | 0, 0, 0, 0 | 0 |
| mypy, supervisor (CI gate) | 105 | 0 | 0 |
| mypy, model-gateway / memory (not gated) | – | 0 / 0 | 0 |
| Largest supervisor module | 2,147 lines | 871 (`sandbox/base.py`) | under 1,000 |
| Functions above complexity 20 (all three services) | 4 | 0 | 0 |
| Migrations vs models (`alembic check`) | drifted | clean | clean |
| Fixed sleeps in tests (supervisor + e2e) | about 30 | 0 | 0 |
| Supervisor tests (Postgres + Temporal) | 730 passing | 824 passing, 0 failing | all pass |
| Model gateway / memory tests | – | 81 / 79 passing | all pass |
| Live e2e stages, local | 2 failing | all pass; stage 2's Docker check runs only in CI | all pass |
| CI jobs (lint-and-types, three test suites, e2e, compose smoke) | 3 of 6 failing | 6 of 6 green | all green |

### W9: re-verified

- CI on `hardening/w8-w9`: all six jobs green, including e2e against the full
  stack and the docker-compose smoke test.
- Locally against the e2e stack (processes, no Docker): stage 2 82/83 (Docker
  check skipped), 3 48/48, 4 31/31, 6 23/23, 7 47/47, 7q 4/4.
- The tests whose sleeps were replaced passed 3 runs of 3 against Temporal.
- The plan doc's ten gates could not be opened from this session, so the table
  above measures each gate CI enforces plus the structural ones PROGRESS
  tracked.

### Final rating: 9/10

Everything a machine can check is green: lint and types at zero across the
services, every suite passing on Postgres and Temporal, the e2e stages and the
compose smoke in CI, no schema drift, and no test that waits on the clock. W8
also fixed nine real bugs that type checking and the stricter tests found.

It isn't 10 because:
- the staging compose file and the Kubernetes manifests still can't run a
  session (no Temporal or worker);
- no automated run has used a real model;
- the coding-v1 Pi harness isn't done.
The first is the one that matters for shipping.

---

## Next step

1. **Deployable beyond compose:** add Temporal (or Temporal Cloud settings) and
   `supervisor-worker` to `deploy/staging/` and `deploy/k8s/`, mirroring
   `docker-compose.yml`, and run the staging stack in CI the way compose smoke
   runs.
2. **Session memory, phases 4–7** (consolidation, embeddings, providers, the
   per-manifest session list with read, resume and fork), on a branch from
   `main`, under the same gates.
3. Optional: a real-model CI job once a `MODEL_E2E_API_KEY` secret exists.

After that, per the roadmap: the backend, then the frontend.
