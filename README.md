# Prime Agent

Build an agent harness in the browser, chat with it, and approve what it does.

| Folder | What it is | Port |
|---|---|---|
| `frontend/` | Next.js app | 3000 |
| `backend/` | FastAPI: users, sign-in, harness records; talks to the supervisor | 8000 |
| `prime-agent-study-main/services/supervisor/` | Runs agent sessions (API, model gateway, memory service, worker) | 8080, 8090, 8092 |

## Run it locally

```sh
./dev.sh
```

It starts Postgres (in Docker unless one already listens on port 5433), Temporal, the
supervisor and its worker, the backend and the frontend, then creates a demo user with one
ready harness. Open http://localhost:3000 and sign in with the login it prints.

First run needs:

- Python venvs: `cd prime-agent-study-main/services/supervisor && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`,
  and the same in `backend/`
- `cd frontend && npm install`
- the [Temporal CLI](https://docs.temporal.io/cli) (`brew install temporal`)
- `127.0.0.1 model-gateway` in `/etc/hosts` (the script offers to add it)

Settings are in `.env.local`, written on first run with fresh secrets. Without a model key
every harness uses the `echo` model, which repeats your message. For a real model, set
`OPENROUTER_API_KEY` there (only the model gateway process receives it) and run `./dev.sh`
again after `./dev.sh stop`.

Other commands: `./dev.sh status`, `./dev.sh logs backend`, `./dev.sh stop`,
`./dev.sh reset` (wipes the databases and local state).

## A tour

Screenshots from `./dev.sh` with a "code-reviewer" agent (two published versions and an
unpublished change). Regenerate them with `cd frontend && node e2e/tour.mjs` (writes `docs/tour/`).

**Overview.** What needs you (approvals waiting), what is running, recent sessions, running
sandboxes, and this week's spend.

![Overview](docs/tour/19-overview.png)

**Your agents.** Every agent you made: its live version and content hash, whether it is
published, and how many sessions run on it. The side panel shows the picked agent's model,
sandbox, budget, tools, what needs approval, and its version history.

![Agents](docs/tour/02-harnesses.png)

**Build it.** The composer has one tab per part of the agent. *Prompt* holds the persona and the
system prompt sections in order (`{cwd}` becomes the working directory). *Tools* picks the
built-in tools and which ones ask before running. *Model* picks a model route. *Sandbox* says where
commands run and what they may touch. *Memory* and *Policy* set compaction, step limits and approvals.

![Prompt tab](docs/tour/03-builder-prompt.png)

A value the platform would refuse is shown on its own tab, before anything is published:

![Policy error](docs/tour/07-builder-policy-error.png)

*Check* runs every check publishing would run (through the supervisor, nothing stored) and
shows each problem on its tab.

![Publish checks](docs/tour/24-publish-checks.png)

**Publish versions.** *Publish* turns the draft into a numbered version that new sessions use.
The side panel lists what changed since the live version and every earlier one.
*Export* and *Import* move an agent between accounts as a file.

![Versions](docs/tour/14-builder-versions.png)

**Run a session.** The conversation streams as it happens: tool calls with their argument and
result, and an approval card when a tool needs you, with a countdown before it is denied.
Messages sent meanwhile queue up. Beside it: the session's sandboxes and their state, spend
against the version's cap, steps and tokens, and the session's files.

![Live session](docs/tour/18-live-session.png)

Tool calls open to show their input and output:

![Tool output](docs/tour/10-chat-tool-output.png)

A refused call says why, in plain words:

![Denied call](docs/tour/11-chat-denied-explained.png)

**Approvals.** Every question a paused run waits on, across your sessions, answered from one page.

![Approvals](docs/tour/20-approvals.png)

**Come back later.** *Sessions* lists every run, with what waits on you on top. Opening one picks
up where it left off, and *Fork from here* starts a new branch at any message.

![Sessions](docs/tour/12-sessions.png)

**Operate.** *Sandboxes* shows each sandbox's state (running, stopped when idle with its files
saved, lost), *Usage* the spend by day, agent and session, and *Policies & secrets* each agent's
approval rules and limits beside the secrets its tools use (names only). *Runners* registers
your own machines for sandboxes, and *Fleet* (owners and admins) shows the workers.

![Sandboxes](docs/tour/21-sandboxes.png)

![Usage](docs/tour/22-usage.png)

**Traces.** Every run as a readable trace. *Traces* lists runs (what was asked, how it ended, the
agent version, where the time went, what it cost) with filters for failures and denials. A run
opens as a transcript: the ask, what the agent did with each command's output and each file
change as a diff, approvals with how long they waited on you, and the answer. *Tree* shows every
step, model call, tool and approval on a timeline, the selected one opened beside it; *Raw
events* the journal it was built from.

![Trace transcript](docs/tour/25-trace-transcript.png)

![Trace tree](docs/tour/26-trace-tree.png)

Every finished run is checked (did it finish, did its tools work, did it loop, were its
approvals allowed, did the tests pass after its last change) and summed up in a sentence. Mark
a run or a step good or wrong, with a note. *Judges* apply a rubric you write to an agent's
runs through its own model; *Issues* groups what failed by agent and cause, with the versions
it hit and the runs to start from. The *Agents* page shows each version's quality.

![Issues](docs/tour/27-trace-issues.png)

Save runs worth keeping to a *Dataset*, and replay it on any version in fresh sessions to
compare versions run by run (replays never approve anything). *Improve* closes the loop: a model
reads an agent's issues and proposes prompt edits, the change is published as a candidate that
is not live, replayed on a dataset beside the live version, and refused unless it does better
on some saved run and worse on none. One that passes waits in *Approvals* with its diff, its
evidence and its results; approving publishes exactly what was measured as the next version.
The optimizer never sees the runs it is measured on and cannot change a judge or a dataset.

![A learned change](docs/tour/28-trace-improve.png)

**Memory.** What each agent remembers about you, in notebooks per version. Add, edit, forget
or search facts. *History* restores a notebook to how it was after any change.

![Memory](docs/tour/15-memory.png)

## Checks

| What | Command |
|---|---|
| Backend tests | `cd backend && .venv/bin/python -m pytest -q tests` (the migration tests need `BACKEND_TEST_PG_URL`, a Postgres) |
| Frontend lint, types, tests | `cd frontend && npx next lint && npx tsc --noEmit && npx vitest run` |
| In the browser (needs `./dev.sh` running) | `cd frontend && npm i --no-save playwright && npx playwright install chromium`, then `node e2e/builder-journey.mjs`, `memory-journey.mjs`, `chat-journey.mjs`, `environment-journey.mjs`, `operate-journey.mjs`, `traces-journey.mjs` and `startup-journey.mjs` |

CI runs all of these on every push, including the browser journeys against a fresh `./dev.sh`.

## How sessions run

The agent never runs inside an API request. The same split is used by production agent
platforms: OpenAI's background responses, xAI's deferred completions, LangGraph's API servers
and queue workers, Claude Managed Agents (where the harness runs apart from the sandbox), and
the OpenAI Agents SDK on Temporal.

| Process | Does |
|---|---|
| Backend (FastAPI, :8000) | Users, harnesses, sessions they own; never runs an agent |
| Supervisor API (:8080) | Accepts commands and starts or signals each session's **Temporal workflow** |
| Worker(s) (`supervisor.temporal.worker`) | Run the workflows: create the session, call the model, run tools in the sandbox |
| Temporal | Keeps every session's progress, so work survives restarts and waits for a worker |

**Starting a session answers at once.** `POST /sessions` returns status `starting` (the
supervisor gets `Prefer: respond-async` and answers `202`). The page then polls
`GET /sessions/{id}/startup` until it reads `ready`, or `failed` with the reason (for example,
Docker not running for a Docker sandbox). The Temporal server answers that status itself, so it
works even when no worker runs. It reports *waiting for an agent worker* when work has been
queued for more than 5 seconds with nobody taking it, and when the setup step keeps failing and
being retried it shows the last error. The start waits in Temporal: when a worker comes back,
the same page carries on by itself. A message sent while the session starts waits for it too.

**Every message is a run with an id.** Sending a message answers at once with its `turn_id`,
which is the run's id (the same id is on the run's journal events). A run is `queued` (behind
another), `in_progress`, `requires_action` (waiting for an approval), then `completed`,
`failed`, `cancelled` or `incomplete` (stopped by a step or budget limit). This is the shape of
OpenAI's background responses and xAI's deferred completions:

| Call | Answers |
|---|---|
| `GET /sessions/{id}/runs/{run_id}` | `202` while queued or going, `200` once ended; always the run: status, final answer, usage, error |
| `GET /sessions/{id}/runs` | The session's runs, newest first |
| `POST /sessions/{id}/runs/{run_id}/cancel` | Stops it; a queued run is dropped and never runs. Safe to repeat: an ended run comes back unchanged |

Any client holding a run id can fetch the result later without keeping the stream open. In the
chat, **Stop** cancels the run going now by its id (pressing it twice, or after the run ended,
does nothing), a queued message has its own **Cancel**, and **Runs** lists each run with its
status, duration, steps, tokens and cost.

**Erasing a session.** `DELETE /api/v1/sessions/{id}/data` ends the session, waits for it and
its background jobs to stop, and erases what the platform keeps of it: its journal and the
payloads stored beside it (unless another session holds the same bytes), the files its runs
produced, its sandboxes' checkpoints, its run traces, its gateway key and the session itself.
Workspace files it attached stay (they are the workspace's), and its sealed workflow history
stays in Temporal until the namespace's retention ends. Ending a session
(`DELETE /api/v1/sessions/{id}`) keeps its record. In the chat, **Erase** does this.

**Deleting an account.** `DELETE /api/v1/users/me` with the account's password (Settings →
General → Delete account) erases everything of the person: the supervisor first ends and erases
every session they own, their long-term memory, the workspaces only they belong to (with every
version of every file) and their tenant's secrets, and removes them from every other workspace;
a workspace they own that others belong to is handed to the member who joined first. Then their
account goes, and with it their harnesses, tools, environments, datasets, experiments and
sign-ins. If the supervisor cannot erase yet (a session still stopping, memory unreachable), the
account is kept and nothing here is removed, so deleting again finishes the job.

### Inside a run

A githi run is a sequence of durable steps that the session's workflow drives (pi-temporal's
stepped mode): a **model step** (one model call, on any worker), then a **round** that answers
each tool call the model asked for (at most 8 at once, read-only calls together), then the next
model step, until the model answers. The workflow's history holds only references. The journal
holds every message, call and result: clients read it, and a retried step reads it back.

- **Talking to a running run.** A message sent while a run works joins it before its next step
  (steering); otherwise it waits behind the run. When no worker takes a message within 10 s,
  Temporal holds it as a signal and it runs once a worker is back.
- **Stopping.** Stop ends the run where it is: its running steps are cancelled, a running
  command is killed, and what the tool did so far is recorded. A call that cannot report in time
  is answered as an unknown outcome.
- **Limits.** A message: 64 K characters. A tool call: 30 min (`TOOL_CALL_TIMEOUT_S`), then it is
  stopped and the run goes on. A run: a step limit (at most 500), a time limit checked between
  steps (`loop.max_run_seconds`), and a hard deadline that stops it mid-call
  (`loop.hard_run_seconds`; waiting for an approval counts toward it). A session: tokens and
  time (`budget.max_tokens_per_session`, `max_seconds_per_session`), and cost
  (`max_cost_per_session`, which counts every model call the session pays for: its steps,
  summaries, subagents and memory's extraction). At most 8 background subagents run at once.
- **When something fails.** A model call that may pass (a rate limit, an overload) is asked
  again on a durable timer. A dead worker is noticed within the heartbeat timeout (30 s; every
  step heartbeats), and its session moves to another worker. A call that may already have run is
  never run twice: it is answered as an unknown outcome, and the model checks. A background
  subagent retried on another worker goes on from the calls it journalled.
- **Long conversations.** Once the conversation outgrows the model's window it is summarised,
  as a run opens or between steps. A summary that fails is retried; if it keeps failing, the
  conversation is kept whole. The summary names the journal entries it replaced, and the model
  can reopen any of them with the `history` tool. A session starts a fresh workflow history
  (continue-as-new) between runs and while idle, so a long-lived session never reaches
  Temporal's history limit.

### Scaling and watching the workers

**One image, two roles.** The supervisor image runs as `python -m supervisor api` (the
default) or `python -m supervisor worker`, the split LangGraph uses for API servers and queue
workers. Scale each on its own: more API replicas for more requests, more workers for more
running sessions. Every worker polls the same Temporal queue, so adding one needs no other change.

| Where | API | Workers |
|---|---|---|
| `./dev.sh` | `supervisor` | `worker` (health on :8091) |
| docker compose | `supervisor` (`command: ["api"]`) | `supervisor-worker`; `docker compose up --scale supervisor-worker=3` |
| Kubernetes | `deploy/k8s/supervisor-deployment.yaml` | `deploy/k8s/worker-deployment.yaml` (with a PodDisruptionBudget); optional backlog autoscaling in `worker-autoscaling.yaml` |

**Per-worker settings:**

| Setting | Default | Does |
|---|---|---|
| `WORKER_MAX_CONCURRENT_RUNS` | 50 | Runs one worker carries at once (its activity slots). Lower it for heavy sandboxes; raise it when workers mostly wait on the model |
| `WORKER_HEALTH_PORT` | off | Serves `/healthz` (fails when heartbeats stop landing) and `/readyz` (fails while starting or draining) for probes |
| `WORKER_BACKLOG_ALERT_S` | 30 | (API) How long queued work may wait before the fleet reads `degraded` |

**Watching the fleet.** `GET /v1/ops/workers` combines what the Temporal server reports (who
polls the queue, how many tasks wait, and how long the oldest has waited) with each worker's
heartbeat (its slots and how many are busy). It answers one of:

- `ok`;
- `degraded`, with alert `backlog` (work waited longer than `WORKER_BACKLOG_ALERT_S`) or `at_capacity` (every slot busy);
- `down`, with alert `no_workers`.

A worker's heartbeat is removed as soon as it stops cleanly and goes stale within 30 seconds of
a crash. Fleet health relies on that rather than on Temporal's poller list, which keeps showing
a stopped worker for a minute or more.

`GET /v1/ops/workers/metrics` serves the same numbers as Prometheus gauges, and
`deploy/alerts/workers.rules.yml` alerts on them. In the product, a banner says when no worker is
running or work is waiting. Runs stay allowed throughout, because work waits in Temporal and
starts as soon as a worker is free. `./dev.sh status` prints the same summary (`workers:
serving, 0/50 runs busy, 0 waiting`), and checks each process by its pid *and* start time, so a
crashed worker shows as `down` and is not mistaken for another process that took over its pid.

### Safe deploys

A deploy should never break a run in progress. This follows Temporal's pattern, the one used
by the OpenAI Agents SDK on Temporal: the workflow holds the plan, and each model call and
tool call is its own step with its own limits. A crash or a deploy costs at most the step in
flight.

**Versioned workers.** Set `WORKER_DEPLOYMENT_NAME` and `WORKER_BUILD_ID` (for example the
image tag), and each deploy's workers register as one version of a Temporal worker deployment.
New sessions start on the deployment's *current* version. Running sessions move to the new
build at their next step (every workflow auto-upgrades), so the old workers can stop as soon as
the new build is current. A version's first worker promotes itself when the deployment has no
current version. After that, `WORKER_ROLLOUT=auto` (the default) promotes each new build once
it polls, and `manual` leaves it to you:

| Command (`python -m supervisor rollout …`) | Does |
|---|---|
| `check` | The pre-deploy gate: replays the histories of running (then recent) workflows against the code about to ship, and exits 1 if any can't be reproduced |
| `status` | Current and ramping builds, each version's state, and which old builds are safe to stop |
| `ramp BUILD PCT` | Sends PCT % of new sessions to BUILD (0 stops the ramp) |
| `promote BUILD` | Makes BUILD current |
| `export DIR` / `check --dir DIR` | Saves histories, or replays saved ones |
| `policies` | Prints the table below |

`GET /v1/ops/rollout` shows the same status. A manual rollout looks like this: run `check`
against production with the new image; start the new workers; `ramp` 10 %; watch
`/v1/ops/workers` and the traces; `promote`; stop the old workers.

**Changing workflow code.** A change that alters which steps a workflow schedules (adding,
removing or reordering one) must be guarded with `workflow.patched("<id>")`, so histories
recorded before it replay the old way. CI enforces this:
`tests/unit/test_release_replay.py` replays every history in `tests/fixtures/histories/`
(recorded by earlier releases) against the current code, and fails on any it can't
reproduce. Record a release's histories after it merges with
`RECORD_HISTORIES=tests/fixtures/histories/r<commit> pytest tests/integration`. Changing a
time limit or a retry is always safe.

**Each step's limits** (from `supervisor/temporal/policies.py`, which the workflows use; a test
keeps this copy current). A step that stops heartbeating is treated as a dead worker. A step
waiting for a session's worker that is gone (60 s placement limit) is adopted by another worker.

<!-- step-policies:start -->
| Step | Does | Where | Time limit | Heartbeat | Retries | If its worker dies |
|---|---|---|---|---|---|---|
| `session.create` | Set up a new session | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker (creating is idempotent) |
| `session.fork` | Copy a session's journal into a new one | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker |
| `session.adopt` | Take over a session whose worker is gone | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker |
| `session.worker_beats` | Before adopting a session: does its worker still heartbeat (busy, not gone)? | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker; if it keeps failing, the session is adopted |
| `session.prepare_turn` | Check budget and recovery before a message runs; start the sandbox (with_run) | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.begin_run` | Open a run: journal the message, plan the steps, summarise earlier turns if due | session worker | 10 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.model_step` | One model call (needs no sandbox, so any worker thinks) | any worker | 10 min | every 30 s | 5 attempts, 1 s doubling to 30 s | heartbeats stop; the model call is retried on another worker |
| `session.compact` | Summarise the conversation inside a run so the next model step fits | session worker | 10 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.compact_counted` | Summarise the conversation inside a run so the next model step fits; what it spent | session worker | 10 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.checkpoint` | Save what the last tools changed in the sandbox (beside the model call) | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | skipped; a lost checkpoint costs a restore, never the turn |
| `session.place_inputs` | Place steering messages in the running run before its next step | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker (each message is keyed, so it lands once) |
| `session.tool_batch` | A round's read-only tool calls, run together in the sandbox | session worker | turn limit (24 h) | every 30 s | 5 attempts, 1 s doubling to 30 s | re-run on the adopting worker (read-only calls; each one's result is kept) |
| `session.tool_call` | One tool call in the sandbox; stopped past `TOOL_CALL_TIMEOUT_S` (30 min), the run going on | session worker | turn limit (24 h) | every 30 s | 5 attempts, 1 s doubling to 30 s (once for at-most-once tools) | re-run on the adopting worker; an at-most-once tool is not: its call is answered as an unknown outcome and the run goes on (the model checks) |
| `session.request_approval` | Ask a person (the wait itself is a durable timer, not a step) | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.resolve_approval` | Record an answer, or the timeout's choice | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.end_run` | Close a run: final answer, usage, why it stopped | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | a call left half done is closed first, then the session is adopted |
| `session.run_turn` | A whole runtime-driven turn (non-stepped harnesses) | session worker | turn limit (24 h) | every 30 s | 3 attempts, 2 s apart | heartbeats stop; retried after adoption, but never resent once tools ran |
| `session.respond_approval` | Deliver an answer to a runtime-driven turn | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.op` | Tool invoke, memory, subagents, workspace | session worker | turn limit (24 h) | every 30 s | 5 attempts, 1 s doubling to 30 s (once for operations with side effects) | an operation with side effects is not re-run: a tool invoke is closed as lost and reported as outcome unknown |
| `session.close_lost_call` | Close a call whose worker died | any worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried on another worker (journal only) |
| `session.terminate` | End the session: stop its runtime and sandbox | session worker | 2 min | every 30 s | 5 attempts, 1 s doubling to 30 s | placement times out; another worker adopts the session and runs it again |
| `session.capture_memory` | Save durable facts from a burst of turns | any worker | 10 min | every 30 s | 5 attempts, 1 s doubling to 30 s | retried elsewhere; a burst that keeps failing is dropped (best effort) |
| `subagent.run_job` | A background subagent job's work | any worker | turn limit (24 h) | every 30 s | 5 attempts, 1 s doubling to 30 s | heartbeats stop; retried on another worker, never lost; a job in a lent hand goes on from the calls it journalled |
| `subagent.end_job` | Record how a background job ended | any worker | 2 min | every 30 s | until it lands, 1 s doubling to 30 s | retried until exactly one end reaches the session's journal |
<!-- step-policies:end -->

### Sandboxes: environments, start and idle stop

This follows Claude Managed Agents: the sandbox definition is kept apart from the agent, the
model doesn't wait for its sandbox, and an idle sandbox costs nothing.

- **Environments** (sidebar → Environments) are named sandbox setups that several harnesses
  share: the provider, file access, approvals, and the two settings below. Start from a preset
  or build your own. In the builder's Sandbox tab, a harness picks an environment instead of
  spelling out its sandbox. Publishing copies the environment's settings into the release, so
  a published version never changes under its sessions. After you edit an environment, each
  harness using it shows unpublished changes until it's republished. An environment can't be
  deleted while a harness uses it.
- **When it starts:**
  - `with_run`: the sandbox starts building as each run begins, alongside the first model
    call, so the first tool call doesn't wait.
  - `on_first_tool`: a sandbox starts only when a tool needs one, so runs that never use a
    tool never start a container.

  The session bar shows which happened (for example *Sandbox ready · started with the run*).
- **When it stops:** a sandbox unused for `idle_stop_s` (default 15 minutes, `0` never) is
  saved and stopped. The session stays, and its next tool call gets a fresh sandbox with the
  files restored. A sandbox isn't stopped while a tool is using it, while another job holds it,
  while the agent has left processes running in it (a server would be lost), or if saving it
  failed.

| Deployment default | Default | For environments that don't say |
|---|---|---|
| `SANDBOX_START` | `on_first_tool` | When sandboxes start |
| `SANDBOX_IDLE_STOP_S` | 900 | Seconds idle before a stop (0: never) |
| `SANDBOX_IDLE_SWEEP_S` | 30 | How often each worker looks for idle sandboxes |

## Workspace files

A workspace owns its files and decides who may see them (*Shared with*). Every save is a new
**version**, and older versions are kept: open a file's history, view or download any version,
or **restore** one. Restoring adds a new version with the old content, so nothing is lost; a
deleted file can be restored the same way. **Folders** are the `/`-separated parts of file
paths, as in S3 and git. *New folder* opens one to upload into, and it stays once it holds a file.

Each version's bytes are stored under a private, opaque key, `workspaces/<scope>/objects/<id>`.
The key contains neither the file's name nor its content hash; names, folders and history live
in PostgreSQL. Identical bytes are stored once **per workspace**. No answer depends on what
another workspace holds, so uploading a file never reveals that someone else has the same one.

### Conversations, runs and artifacts

Each part has one job:

| Part | What it decides |
|---|---|
| Workspace | Who owns the files and who may see them |
| File and version | Durable content: every save is a version, kept with its author and source |
| Attachment | A workspace file used in one conversation, **pinned to a version** |
| Run | What one turn started from: its attachments and every workspace file, by version (`run_inputs`) |
| Artifact | A file a run made for the user, and which run made it |
| Sandbox | Where the agent works for now; never the place anything is kept |

**Attach** a file in *This conversation*, from the workspace or by uploading it (it is saved
in the workspace's `Attachments` folder first). The agent reads exactly that version,
read-only, in `.attachments/`. A note after the harness's own prompt tells the model where the
attachments are and that files it makes for the user go in `.outputs/`.

When a run ends, the supervisor (never the agent) **collects** what is new or changed in
`.outputs/`. It reads each file through the sandbox's path checks, so a symlink can't reach
outside the sandbox. It caps each file at 100 MiB and 50 files a run, stores each one under a
private key (`sessions/<session>/artifacts/<id>`), and records which run made it. *Save* puts
an artifact into a workspace folder as a new version whose source is that artifact. The agent's
own edits to workspace files are copied back at the end of the run as versions marked as its
own.

`.attachments/` and `.outputs/` are the session's folders, not workspace paths: they are never
copied into the workspace, refreshing the workspace leaves them alone, and uploads refuse them.

Routes (backend, per session): `GET/POST .../sessions/{id}/attachments`,
`DELETE .../attachments/{attachment}`, `GET .../artifacts`, `GET .../artifacts/{artifact}/content`,
`POST .../artifacts/{artifact}/save` with `{"path": "Reports/q4.md"}`, and
`GET .../runs/{turn}/inputs`.

### Uploads

Files go straight from the browser to object storage, and the API servers never hold the
bytes. This is the usual production pattern for S3-backed apps, and it's how OpenAI's Uploads
API is shaped:

1. **Start:** `POST /workspace/scopes/{scope}/uploads` with the path, size, type and SHA-256
   (which the browser computes). The supervisor checks the grant, the path, the size limit and
   the expected version. It answers with a short-lived signed PUT URL for a staging key
   (`uploads/<scope>/<id>`) and a signed ticket. If this workspace already holds those bytes,
   the new version is recorded at once and nothing is sent.
2. **Upload:** the browser PUTs the file to that URL, showing progress. The URL signs the
   length, type and SHA-256 checksum, so S3 refuses any other bytes.
3. **Complete:** `POST .../uploads/complete` with the ticket. The supervisor measures what
   arrived: S3's own checksum, or a streamed SHA-256 on stores that don't keep one (MinIO,
   SeaweedFS). It deletes the upload if it doesn't match. Otherwise it copies it inside the
   store to the new version's private key and records the version, along with who saved it.
   The client only ever writes its staging key, never a recorded version.

If the store can't sign URLs, or the page isn't on https/localhost (where browsers can't hash
files), the file goes through `PUT .../files/{path}` instead. It's read in chunks up to the
same limit.

History: `GET .../history/{path}` lists versions, `GET .../history/{path}/@{n}` returns one,
and `POST .../restore/{path}` with `{"version": n}` restores one.

For production:

| Setting | What it does |
|---|---|
| `WORKSPACE_MAX_UPLOAD_BYTES` (supervisor and backend) | Largest file (default 100 MiB) |
| `OBJECT_STORAGE_PUBLIC_ENDPOINT` | The address browsers use to reach the store, when the supervisor reaches it by another (e.g. a compose service name) |
| `WORKSPACE_UPLOAD_SECRET` (or `CAPABILITY_SECRET`) | Signs upload tickets; every supervisor API replica needs the same value |
| Bucket CORS | Allow `PUT` from the app's origin with headers `Content-Type` and `x-amz-checksum-sha256` (`./dev.sh` sets this locally) |
| Bucket lifecycle rule | Expire objects under `uploads/` after a day, which removes abandoned uploads (never apply it to `workspaces/`) |

Files saved before this change keep their old storage keys and are read from there; their
current state is their first recorded version.
