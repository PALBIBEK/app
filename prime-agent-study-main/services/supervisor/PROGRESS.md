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
- **Test suite: 684 passing, 0 failing** (from 228 passing at the start); the
  model gateway's own suite: 81 passing. Two tests are intermittent and are run
  separately: `test_memory_histories_replay_deterministically` (times out under
  full-suite load) and `test_reconnect_mid_stream_no_gaps_no_dupes` (SQLite
  "database is locked" in its 100-round loop). Temporal-backed tests need
  `TEMPORAL_CLI_PATH` where the dev-server download is blocked.
- **Three user goals are not met yet:** registering or reading a harness through
  the API, long-term memory across sessions, and keeping one user's sessions
  away from another user in the same tenant.

---

## Known gaps

Found by the live tests (numbers as in the report):
- **Security and audit.** The catalog has no tenant ownership (F10). Strict
  multi-tenant mode is bypassed by a local_process sandbox profile, and a
  missing e2b package falls back to local_process (F19). Session access is
  tenant-wide; the stored owner is never checked (F29). Approvals record no
  responder (F30).
- **Long-term memory doesn't work** (F27): recall always returns 404, and facts
  are scoped per session.
- **Harnesses can't be read or registered through the API** (F5, F6); no list
  APIs for harnesses or tools (F4, F12).
- **No runnable default.** The shipped githi-v1 and coding-v1 pin a placeholder
  image (F3); API and worker race to create the schema (F1); compose has no
  memory service (F2); the default memory profile's summarizer is unserved and
  its sizes don't fit small windows (F24–F26).
- **Sandboxes.** Concurrent scope writes overwrite each other (F20); a session
  without a scope loses its files when its worker dies (F21).
- **Publish accepts configs that can never work** (F11, F17, F28). One loop-guard
  trip fails the whole session (F32).

Earlier:
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
- **Two intermittent tests** (see above) need a real fix, not a retry.

---

## Next step

**Merge the stack, then close the security gaps the live tests found.** In order:

1. Merge PRs #2 to #6, so main carries the six live fixes.
2. Ownership and audit: tenant ownership on catalog artifacts, owner checks on
   session routes, the responder on every approval (F10, F29, F30).
3. Strict-mode sandboxes checked per session; fail boot instead of falling back
   (F19).
4. Long-term memory: the memory service's own session id, facts scoped to the
   user (F27).
5. Harness management through the API: read bodies, register manifests, list
   harnesses and tools (F5, F6, F4, F12).
6. A deployable default: migrations, a real image, the memory service in
   compose, a served summarizer; then Temporal in the staging compose and
   Kubernetes manifests (F1, F3, F2, F24).
7. A real-model pass and a dedicated failure-injection pass.

After that, per the roadmap: the backend, then the frontend.
