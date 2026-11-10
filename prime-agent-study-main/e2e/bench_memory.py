"""Memory under load on a running stack: many tenants, agents and concurrent sessions.

    e2e/bench_memory.py run [--tenants 4] [--compositions githi-v1,...] [--sessions 50]
                            [--facts 3] [--capture-model ROUTE] [--quiet 5] [--results PATH] [--resume]
    e2e/bench_memory.py report [PATH] [--run ID]

The load harness of ``docs/memory-gaps.md`` (G0): every memory gap is verified against it,
and its scorecard is kept in the gap's PR so the next gap can be compared with it.

For each tenant × agent (a composition) × session, at once (``--sessions`` concurrent
sessions per tenant and agent; every ``--per-owner`` of them are one user's, as one person
runs several sessions at once, so they write one notebook concurrently):

1. **write**: the session adds ``--facts`` facts of its own and one fact all of its
   owner's sessions add, with the ``memory`` tool (no model needed). Each fact carries a
   token naming its run, owner (so its tenant and agent), session and attempt.
2. **recall**: a new session of the same owner asks about its facts. What was injected
   (``MemoryInjected``) is recorded, and two times: the recall step itself (the first
   model step's ``turn_start`` to ``MemoryInjected``, the target's measure) and the whole
   wait before the model is called (``run_start`` to ``MemoryInjected``: the audience
   checkpoint and the step's scheduling on a worker too, which grows when workers are
   short).
3. **capture** (``--capture-model``, which needs a real model): a session states a fact
   in plain words, with automatic capture after ``--quiet`` seconds of quiet; the time
   until the fact is in the owner's notebook is the capture lag.

When every session is done, each owner's notebook is read once more, and the checks run
on that final read:

* every fact a session wrote, and its owner's shared fact, is there exactly once (lost,
  duplicated): the shared fact is written by every session of its owner, at once, and by
  any retry, so a second copy is memory failing to deduplicate, not the harness; an owner
  whose final read failed is *unverified*, never scored from an earlier, partial read;
* each session's own facts were in its owner's next recall, when recall had room
  (unrecalled); a run that recalled nothing is a session error;
* no notebook or injection holds another owner's token (crossed);
* the latencies are within their targets; a target with no measurement fails.

A run is fresh unless ``--resume`` is given: it then goes on with the file's last run,
skipping its finished sessions and running its failed ones again (as a new attempt, with
tokens of its own). A report scores one run (the file's last, or ``--run``), each session
by its latest attempt. Both subcommands exit 1 when a check fails.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import threading
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from client import User, payloads, runs_ended

try:  # the stack's own Python has the supervisor installed: its budget, not a copy
    from supervisor.memory_service.notebooks.types import RECALL_MAX_CHARS
except ImportError:  # pragma: no cover — run with another interpreter
    RECALL_MAX_CHARS = 6_000

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "memory_bench" / "results" / "results.jsonl"
TARGETS = {
    "recall_ms_p95": 150,  # the recall step of a run's first model step
    "lost": 0,
    "duplicated": 0,
    "crossed": 0,
    "unrecalled": 0,
    "unverified": 0,
}
"""``docs/memory-gaps.md``'s targets. Capture lag's is 2 × the quiet window + 30 s."""
ROOM = 0.9
"""An injection shorter than this share of recall's budget had room for every fact, so a
missing one was not ranked out: it is unrecalled."""
TOKEN = re.compile(r"zq[0-9a-f]+-[A-Za-z0-9.-]+?qz")
"""Every token in a text (``token``'s shape), read once instead of searched per token."""
_WRITE = threading.Lock()

type Row = dict[str, Any]


def token(run: str, owner: str, what: str) -> str:
    """A word naming exactly one fact's place: found in another owner's notebook or
    injection, it crossed a boundary. It ends in a marker no part of it holds, so no token
    is a prefix of another (``f1`` of ``f10``)."""
    return f"zq{run}-{owner}-{what}".replace("_", "") + "qz"


def owner_of(run: str, tenant: str, agent: str, session: int, per_owner: int) -> str:
    """The user a session runs as: ``per_owner`` sessions each, new users every run, so one
    run's notebooks hold only its own facts."""
    # No part of a token may hold its end marker, or ``TOKEN`` would stop inside it.
    return f"{tenant}-{agent}-{run}-u{session // per_owner:03d}".replace("qz", "q-z")


def pin(composition: dict[str, Any]) -> str:
    return f"composition:{composition['artifact_id']}@{composition['digest']}"


# --- talking to the stack ---------------------------------------------------------------


def _ok(response: Any) -> Any:
    """The JSON of a successful answer; a refusal or an outage raises with what it said."""
    if response.status_code != 200:
        raise RuntimeError(f"{response.request.url.path} {response.status_code}: {response.text[:200]}")
    return response.json()


def session(user: User, composition: dict[str, Any], model: str | None = None, **memory: Any) -> str:
    extra: dict[str, Any] = {"memory": memory} if memory else {}
    if model:
        extra["model"] = model
    r = user.create(composition["artifact_id"], composition["digest"], **extra)
    if r.status_code != 201:
        raise RuntimeError(f"create {r.status_code}: {r.text[:200]}")
    return str(r.json()["session_id"])


def turn(user: User, sid: str, text: str, n: int, timeout: float) -> list[dict[str, Any]]:
    """Send message ``n`` of the session and return the events of its run."""
    r = user.say(sid, text)
    if r.status_code != 202:
        raise RuntimeError(f"message {r.status_code}: {r.text[:200]}")
    events = user.wait(sid, runs_ended(n), timeout=timeout)
    starts = [i for i, e in enumerate(events) if e.get("type") == "run_start"]
    return events[starts[n - 1] :] if len(starts) >= n else events


def notebook_facts(user: User, composition: dict[str, Any]) -> list[str]:
    """Every fact text in the owner's notebooks for this agent."""
    texts: list[str] = []
    for nb in _ok(user.get("/v1/memory/notebooks", params={"agent_pin": pin(composition)})):
        texts += [str(f.get("text", "")) for f in _ok(user.get(f"/v1/memory/notebooks/{nb['id']}/facts"))]
    return texts


def injected_text(user: User, sid: str, run: list[dict[str, Any]]) -> str:
    """What the run's recall showed the model. The event stream carries only a count (the
    supervisor keeps memory's text off it); the run's recorded events hold the text."""
    run_id = next((e.get("turn_id") for e in run if e.get("type") == "run_start"), None)
    recorded = _ok(user.get(f"/v1/sessions/{sid}/runs/{run_id}/trace/events")).get("events", [])
    return " ".join(str(e["payload"].get("text", "")) for e in recorded if e.get("event_type") == "MemoryInjected")


def since(run: list[dict[str, Any]], start: str, end: str = "memory_injected") -> int | None:
    """Milliseconds from the run's first ``start`` event to its first ``end`` event."""
    began = next((e["ts_ms"] for e in run if e.get("type") == start), None)
    shown = next((e["ts_ms"] for e in run if e.get("type") == end), None)
    return shown - began if began is not None and shown is not None else None


def wait_for_capture(user: User, composition: dict[str, Any], said: str, began: float, args: argparse.Namespace) -> float | None:
    """Seconds from ``began`` until ``said`` is in the owner's notebook, or None by the
    deadline. Polls back off, and a failed poll is retried, not a failed session: polling
    must neither load the stack it measures nor turn a transient error into a lost fact."""
    deadline, wait = began + 2 * args.quiet + 120, 1.0
    time.sleep(args.quiet)  # the quiet window, from the turn's end: nothing is captured before it
    while time.time() < deadline:
        try:
            if any(said in f for f in notebook_facts(user, composition)):
                return round(time.time() - began, 1)
        except (RuntimeError, httpx.HTTPError):
            pass  # a refused, failed or timed-out read: the next poll asks again
        time.sleep(wait)
        wait = min(wait * 2, 10.0)
    return None


# --- one session ------------------------------------------------------------------------


def cell(args: argparse.Namespace, run_id: str, tenant: str, agent: str, composition: dict[str, Any], s: int,
         attempt: int) -> Row:
    """One session's whole script, as one result row."""
    owner = owner_of(run_id, tenant, agent, s, args.per_owner)
    user = User(tenant, owner)
    mine = [token(run_id, owner, f"s{s}a{attempt}f{i}") for i in range(args.facts)]
    out: Row = {"run": run_id, "tenant": tenant, "agent": agent, "session": s, "attempt": attempt, "owner": owner,
                "mine": mine, "shared": token(run_id, owner, "shared"), "error": None}
    try:
        sid = session(user, composition)
        facts = [{"text": f"The locker word is {t}."} for t in [*mine, out["shared"]]]
        run = turn(user, sid, "/tool memory " + json.dumps({"action": "add", "facts": facts}), 1, args.timeout)
        ends = payloads(run, "ToolEnd")
        out["write_ok"] = bool(ends) and (ends[0].get("result") or {}).get("ok") is True
        if not out["write_ok"]:  # never a clean session with its facts unscored
            raise RuntimeError(f"the memory tool did not write: {(ends[0].get('result') if ends else 'no ToolEnd')!r:.200}")

        later = session(user, composition)
        run = turn(user, later, "What are my locker words?", 1, args.timeout)
        if not payloads(run, "MemoryInjected"):
            raise RuntimeError("the later session's run recalled nothing (no MemoryInjected)")
        out["recall_ms"] = since(run, "turn_start")  # the recall step itself
        out["first_step_ms"] = since(run, "run_start")  # with the checkpoint and the step's scheduling
        out["injected"] = injected_text(user, later, run)

        if args.capture_model:
            out["said"] = token(run_id, owner, f"s{s}a{attempt}said")
            spoke = session(user, composition, args.capture_model, capture_quiet_s=args.quiet, capture_max_turns=1)
            turn(user, spoke, f"Please remember for later: my locker word is {out['said']}.", 1, args.timeout)
            # From the turn's end, where capture's quiet window starts (not the model's reply).
            out["capture_s"] = wait_for_capture(user, composition, out["said"], time.time(), args)
    except Exception as exc:  # noqa: BLE001 — a session's failure is its row, the load goes on
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        user.http.close()
    return out


# --- the run ----------------------------------------------------------------------------


def _rows(path: Path) -> list[Row]:
    """The results file's rows; a line cut off by a crash (or a full disk) is skipped."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def run(args: argparse.Namespace) -> bool:
    """Run the load and report it; whether every check holds."""
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    run_id, earlier = uuid.uuid4().hex[:6], []
    if args.resume and results.exists() and (rows := _rows(results)):
        run_id = rows[-1]["run"]  # its tokens are in the notebooks
        earlier = [r for r in rows if r.get("run") == run_id and "final" not in r]
    attempts: dict[tuple[str, str, int], int] = {}
    done: set[tuple[str, str, int]] = set()
    for r in earlier:
        key = (r["tenant"], r["agent"], int(r["session"]))
        attempts[key] = max(attempts.get(key, 0), int(r.get("attempt", 1)))
        if not r.get("error"):
            done.add(key)
    tenants = [f"bench{t}" for t in range(args.tenants)]
    names = args.compositions.split(",")
    admin = User(tenants[0], "bench-admin")
    try:
        compositions = {name: admin.latest("composition", name) for name in names}
    finally:
        admin.http.close()
    cells = [(t, a, s) for t in tenants for a in names for s in range(args.sessions)]
    todo = [c for c in cells if c not in done]
    print(f"run {run_id}: {len(cells)} sessions ({len(tenants)} tenants x {len(names)} agents x {args.sessions}), "
          f"{len(cells) - len(todo)} already done", flush=True)
    began = time.time()

    def write(row: Row) -> None:
        with _WRITE, results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def one(c: tuple[str, str, int]) -> None:
        row = cell(args, run_id, c[0], c[1], compositions[c[1]], c[2], attempts.get(c, 0) + 1)
        write(row)
        if row["error"]:
            print(f"  {c[0]}/{c[1]}/{c[2]}: {row['error'][:160]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(one, todo))
    # Each owner's notebook once every session is done (a session that ended early read it
    # before its owner's other sessions wrote): what lost and duplicated are judged on.
    for t, a, owner in sorted({(t, a, owner_of(run_id, t, a, s, args.per_owner)) for t, a, s in cells}):
        final: Row = {"run": run_id, "final": owner, "tenant": t, "agent": a}
        reader = User(t, owner)
        try:
            final["notebook"] = notebook_facts(reader, compositions[a])
        except Exception as exc:  # noqa: BLE001 — the owner is then unverified, not scored
            final["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            reader.http.close()
        write(final)
    print(f"done in {time.time() - began:.0f}s", flush=True)
    return report([str(results)], quiet_s=args.quiet, run_id=run_id)


# --- the scorecard ----------------------------------------------------------------------


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else float("nan")


def count(word: str, texts: Iterable[str]) -> int:
    """How many of ``texts`` hold ``word``."""
    return sum(word in text for text in texts)


def within(value: float | None, limit: float) -> bool:
    """A measurement within its target; no measurement (None, NaN) is not within it."""
    return value is not None and not (isinstance(value, float) and math.isnan(value)) and value <= limit


def report(paths: list[str], quiet_s: float = 5.0, run_id: str | None = None) -> bool:
    """One run's scorecard (``run_id``, else the last run in the files), each session by its
    latest attempt; whether every check holds."""
    every = [row for p in paths for row in _rows(Path(p))]
    run_id = run_id or (every[-1]["run"] if every else None)
    every = [r for r in every if r.get("run") == run_id]
    latest: dict[tuple[str, str, int], Row] = {}
    for r in every:
        if "final" not in r:
            key = (r["tenant"], r["agent"], int(r["session"]))
            if int(r.get("attempt", 1)) >= int(latest.get(key, {}).get("attempt", 0)):
                latest[key] = r
    rows = list(latest.values())
    if not rows:
        print("no rows")
        return False
    finals = {r["final"]: r["notebook"] for r in every if "final" in r and "notebook" in r}  # the latest read wins
    by_owner: dict[str, list[Row]] = {}
    for r in rows:
        by_owner.setdefault(r["owner"], []).append(r)
    owner_by_token = {
        t: r["owner"] for r in every if "final" not in r for t in [*r.get("mine", []), r.get("shared", ""), r.get("said", "")] if t
    }

    lost = duplicated = crossed = unrecalled = unverified = 0
    for owner, sessions in by_owner.items():
        writers = [r for r in sessions if r.get("write_ok")]
        if owner not in finals:
            unverified += bool(writers)  # no final read: nothing about it is scored
            continue
        notebook = finals[owner]
        for t in [t for r in writers for t in r["mine"]] + ([writers[0]["shared"]] if writers else []):
            n = count(t, notebook)
            lost += n == 0
            duplicated += n > 1
        seen = set(TOKEN.findall(" ".join(notebook) + " " + " ".join(r.get("injected", "") for r in sessions)))
        crossed += sum(1 for t in seen if owner_by_token.get(t) != owner)  # a token no session made crossed too
    for r in rows:
        injected = r.get("injected")
        if r.get("error") or injected is None or len(injected) >= ROOM * RECALL_MAX_CHARS:
            continue  # recall ran out of room: a fact ranked out of it is not unrecalled
        unrecalled += any(t not in injected for t in r["mine"])

    recalls = [float(r["recall_ms"]) for r in rows if r.get("recall_ms") is not None]
    firsts = [float(r["first_step_ms"]) for r in rows if r.get("first_step_ms") is not None]
    asked = [r for r in rows if r.get("said")]
    captures = [float(r["capture_s"]) for r in asked if r.get("capture_s") is not None]
    errors = [r for r in rows if r.get("error")]
    score = {
        "sessions": len(rows),
        "retried": sum(1 for r in rows if int(r.get("attempt", 1)) > 1),
        "errors": len(errors),
        "lost": lost,
        "duplicated": duplicated,
        "crossed": crossed,
        "unrecalled": unrecalled,
        "unverified": unverified,
        "recall_ms_p50": pct(recalls, 0.5),
        "recall_ms_p95": pct(recalls, 0.95),
        "first_step_ms_p50": pct(firsts, 0.5),
        "first_step_ms_p95": pct(firsts, 0.95),
        "capture_s_p50": pct(captures, 0.5) if asked else None,
        "capture_s_p95": pct(captures, 0.95) if asked else None,
        "captured": f"{len(captures)}/{len(asked)}" if asked else None,
    }
    checks = {"no session errors": not errors, **{f"{k} <= {v}": within(score[k], v) for k, v in TARGETS.items()}}
    if asked:
        checks[f"capture_s_p95 <= {2 * quiet_s + 30:g}"] = within(score["capture_s_p95"], 2 * quiet_s + 30)
        checks["every stated fact captured"] = len(captures) == len(asked)
    print(json.dumps(score, indent=2))
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    for r in errors[:10]:
        print(f"  error {r['tenant']}/{r['agent']}/{r['session']}: {r['error'][:160]}")
    return all(checks.values())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--tenants", type=int, default=4)
    go.add_argument("--compositions", default="githi-v1", help="the agents, one composition each (5 for the default load)")
    go.add_argument("--sessions", type=int, default=50, help="concurrent sessions per tenant and agent")
    go.add_argument("--per-owner", type=int, default=5, help="concurrent sessions per user (they share a notebook)")
    go.add_argument("--facts", type=int, default=3)
    go.add_argument("--concurrency", type=int, default=50, help="sessions in flight at once")
    go.add_argument("--capture-model", default="", help="also measure automatic capture (needs a real model route)")
    go.add_argument("--quiet", type=float, default=5.0, help="capture quiet window, seconds")
    go.add_argument("--timeout", type=float, default=180)
    go.add_argument("--results", default=str(RESULTS))
    go.add_argument("--resume", action="store_true", help="go on with the file's last run instead of a new one")
    show = sub.add_parser("report")
    show.add_argument("paths", nargs="*", default=[str(RESULTS)])
    show.add_argument("--quiet", type=float, default=5.0)
    show.add_argument("--run", default=None, help="the run to score (default: the file's last)")
    args = parser.parse_args()
    passed = run(args) if args.command == "run" else report(args.paths, quiet_s=args.quiet, run_id=args.run)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
