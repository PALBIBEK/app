# Why the Session Supervisor Looks Like This

## The one idea behind everything

This service does **not** run the agent. Pi runs the agent.

This service keeps the agent alive, remembers what it did, and stops it when
it misbehaves. It is a babysitter, not a brain.

Almost every folder exists because of that split.

---

## Why it's called "supervisor" and not "orchestrator"

Two different things were both called orchestration:

- **Inside Pi:** deciding which tool to call next, building the prompt, looping.
- **In this service:** starting sessions, saving history, recovering crashes.

Same word, different jobs. We call ours the **supervisor** so nobody has to ask
which one you mean.

---

## The folders

### `agent/` — AXIS 1: what agent runs

`base.py` is the only surface the rest of the supervisor depends on
(`AgentRuntime`, `AgentState`, `get_capabilities`). Concrete runtimes live under
`runtimes/` and are named **only** in `factory.py`.

Normative seam law: [`contracts/agent_core.md`](../../contracts/agent_core.md)
(Definition / Provider / Consumer + `RuntimeSeams` Consumer stance). Agent-core
**consumes** tools/prompts/subagents/model gateway plugs; it does not own them.

### `agent/runtimes/pi/` — the only place that speaks Pi's language

Pi talks over stdin/stdout in JSON lines. That protocol will change when Pi
upgrades.

Keeping all of it in one folder means a Pi upgrade touches **one folder**,
not the whole codebase. Everything else just calls Python functions and never
sees the wire format. Future runtimes (non-Pi) are siblings under `runtimes/`.

### `sandbox/` — AXIS 2: where code runs

`base.py` defines what a sandbox must do. Providers implement it:

- `none.py` — null impl for sandbox-free agents (`requires_sandbox: false`)
- `local_process.py` / `local_docker.py` / `e2b.py` — real backends

The rest of the code depends on the interface, never on Docker or E2B
directly. You can change vendors by writing one file.

### `journal/` — the memory that survives a crash

If the service dies mid-task, we need to know: *did that `npm install`
actually run?*

The journal writes down what we're **about to do** before we do it, and what
happened after. A record with a start but no finish means "we don't know" —
and knowing that we don't know is the whole point.

It's separate from `session/` because one is about **facts** and the other is
about **decisions**.

### `session/` — the lifecycle

Create, resume, fork, kill. Also: is this session warm (sandbox still alive),
cold (needs rebuilding), or gone?

`modes.py` chooses sticky vs. stateless-per-turn (coding harnesses stay warm;
sandbox-free agents typically spawn per turn). Recovery decisions live here,
using facts from `journal/`.

### `harness/` — protection against the agent changing under you

The "harness" is the agent's behavior: system prompt, tools, Pi version,
extensions. You will change it constantly as you experiment.

A session is **pinned** to one harness version when it's created and never
moves. Otherwise you resume a conversation and the agent suddenly has
different tools than the ones in its own history — and gets confused.

Version files live in repo-root `harness/versions/` (`coding-v1`, `kg-v1`, …)
and declare `requires_sandbox`. `compat.py` refuses unknown
`protocol_version`s so you fail immediately instead of halfway through a
session.

### `policy/` — the limits

Budgets, approvals, loop detection. All the "stop" rules in one place.

Separate because the agent must never be able to skip these. If they were
mixed into the agent code, they'd be suggestions. Here, they're rules.

### `api/` — the outside world

HTTP and SSE. Kept thin on purpose — it translates requests and streams events,
nothing more. All real logic lives deeper.

### `db/` and `obs/` — plumbing

Database access and metrics/tracing. Boring by design.

---

## `contracts/` sits outside both

The supervisor and the harness have to agree on a protocol. That agreement
belongs to neither of them.

If it lived in `harness/`, the supervisor would depend on the harness repo.
If it lived in `supervisor/`, the harness would. Top-level means it's shared,
and it's the obvious place to look when Pi's format changes.

---

## `services/` for one service

Feels like overkill today. But Workspace and Model Gateway are coming, and
adding a sibling folder is easier than pulling a service out of a codebase
that assumed there was only ever one.

---

## The short version

| Folder | Why it exists |
|---|---|
| `agent/` | What runs — `AgentRuntime` + capabilities |
| `agent/runtimes/pi/` | Pi's protocol will change — contain it |
| `sandbox/` | Where code runs will change — abstract it (`none` included) |
| `journal/` | Crashes happen — record intent before action |
| `session/` | Warm/cold resume + sticky vs. per-turn modes |
| `harness/` | Agent behavior changes often — pin it per session |
| `policy/` | Limits the agent can't skip |
| `api/` | Thin translation layer |
| `contracts/` | Belongs to neither side |

**Rule of thumb:** the supervisor changes rarely, the harness changes weekly.
The folder structure exists to keep those two speeds apart.

---

## Related notes

- `../../Experiments/pi-supervisor-event-model.md` — Pi RPC transcript → framing / journal / policy
- `../../Experiments/message-end-usage-budget.md` — `message_end.usage` → `policy/budget.py`
---

## Step 9 — Sandbox measurements (Phase 1)

Recorded against the harness image (`harness/images/Dockerfile`, digest-pinned).
Run `services/supervisor/scripts/measure_sandbox_metrics.py` on a Docker host and
paste results into the **Measured** column. See also `docs/cold-resume-slo.md`.

| Metric | Why it matters | Measured | Notes |
|---|---|---|---|
| cold start (`docker run -d --init`) | user-visible latency on first turn | *run script* | must use `--init` |
| clone + `npm ci` | dominates cold start on real repos | *TBD* | use a representative coding repo |
| runtime startup → first event | per-turn cost in stateless mode | *TBD* | `pi --mode rpc` + `get_state` |
| peak RSS (idle / under turn) | × concurrency = infra bill | *TBD* | `docker stats --no-stream` |
| `docker commit` | snapshot-on-idle cost | *TBD* | filesystem only — process dies |
| restore from commit | whether aggressive reaping pays off | *TBD* | cold path from Step 5 |
| E2B pause / resume | remote reap arithmetic | *TBD* | ~seconds/GiB pause, ~1s resume (vendor guidance) |

### Phase 1 operational knobs

| Env | Default | Purpose |
|---|---|---|
| `IDLE_REAP_MAX_SECONDS` | `0` (off) | Pause sticky / terminate stateless when idle |
| `IDLE_REAP_INTERVAL_SECONDS` | `60` | Background reaper interval |
| `DRAIN_GRACE_S` | `30` | Deploy drain wait for settled turns |

### Decision these numbers make

- **Reap threshold.** If restore ≪ idle memory cost, reap aggressively. If
  restore is tens of seconds on a warm repo, hold containers longer.
- **Sticky vs. stateless.** Coding harness stays sticky when runtime startup is
  seconds and the workspace is warm. KG / sandbox-free stays stateless.

### Provider selection

```
SANDBOX_PROVIDER=none | local_process | local_docker | e2b
```

Factory is `sandbox/factory.py` — the only place provider names appear.
`session/`, `journal/`, and `api/` must stay free of docker/e2b imports.
