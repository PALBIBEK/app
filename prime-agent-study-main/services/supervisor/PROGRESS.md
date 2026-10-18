# Supervisor: progress log

Where the supervisor stood when this work began, what has been done since, and
what comes next. `fix/supervisor-boot` and `feat/agent-pipeline-stages` are
merged into `main`; `chore/clean-supervisor` (stale tests and docs removed) and
`feat/tool-registry` (sections 9 and 10) build on it.

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

### Where it stands now
- **Every session is durable by default,** whatever seams it was composed
  from:
  - githi with a model gateway is durable step by step;
  - any other runtime is durable turn by turn (a crash mid-turn ends that turn
    as interrupted rather than resuming it).
- **Tools a composition adds** that the manifest doesn't classify fall back to
  `recovery.default` (`fail` for githi), so they run at most once and are never
  automatically re-run.
- **Test suite: 560 passing, 0 failing, 0 that cannot be imported** (from 228
  passing at the start). Two tests are intermittent and are run separately:
  `test_memory_histories_replay_deterministically` (can time out under full
  suite load) and `test_reconnect_mid_stream_no_gaps_no_dupes` (SQLite
  "database is locked" in its 100-round loop). The Temporal suites can also
  time out once under load and pass on rerun.

---

## Known gaps

- **Two parallel session error hierarchies.** `session/core/errors.py` and
  `session/manager.py` both define `SessionNotFound`, `RecoveryBlocked` and
  others. The workflow paths match errors by name, so they're unaffected, but
  the remaining in-process code paths still catch the wrong class.
- **Continuable subagents** (`start_continuable`, child messages) still hold
  live state in the provider. No built-in provider supports them except ACP,
  whose state lives on the remote server.
- **A compaction interrupted mid-way** leaves the session waiting for human
  review on resume (the existing design).
- **The per-turn token limit** is counted in the worker's memory, so a worker
  restart mid-turn resets it. The cost limit is exact.
- **Deployment.** The staging compose files and Kubernetes manifests don't
  include Temporal yet. `docker compose` itself hasn't been run in this
  environment (no Docker here).
- **No shell in a sandbox yet.** File tools work on a sandbox workspace, but
  `bash` and `git_commit` return `UNSUPPORTED_CAPABILITY` there: the tool
  `Workspace` is not yet wired to the sandbox's `exec`.
- **Two intermittent tests** (see above) need a real fix, not a retry.

---

## Next step

**Make the supervisor deployable with Temporal everywhere, then clean up what
the gaps list.** In order:

1. Add Temporal and the `supervisor-worker` to the staging compose files and
   the Kubernetes manifests, run `docker compose up` for real, and run the
   durability scenarios against that stack.
2. Unify the session error hierarchies into one (`session/core/errors.py`), and
   remove the duplicate `session/operations/subagent_ops.py`.
3. Give the tool `Workspace` a sandbox implementation backed by the sandbox's
   `exec`, so `bash` and `git_commit` run inside the sandbox.
4. Fix the two intermittent tests.

After that, per the roadmap: the backend, then the frontend.
