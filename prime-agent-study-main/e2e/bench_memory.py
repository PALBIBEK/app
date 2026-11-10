"""Memory under load on a running stack: many tenants, agents and concurrent sessions.

    e2e/bench_memory.py run [--tenants 4] [--compositions fx-full,...] [--sessions 50]
                            [--facts 3] [--capture-model ROUTE] [--quiet 5] [--results PATH]
    e2e/bench_memory.py report [PATH]

The load harness of ``docs/memory-gaps.md`` (G0): every memory gap is verified against it,
and its scorecard is kept in the gap's PR so the next gap can be compared with it.

For each tenant × agent (a composition) × session, at once (``--sessions`` concurrent
sessions per tenant and agent; every ``--per-owner`` of them are one user's, as one person
runs several sessions at once, so they write one notebook concurrently):

1. **write**: the session adds ``--facts`` facts of its own and one fact all of its
   owner's sessions add, with the ``memory`` tool (no model needed). Each fact carries a
   token naming its tenant, agent, owner and session.
2. **recall**: a new session of the same owner asks about its facts; what was injected
   (``MemoryInjected``) is recorded, and two times: the recall step itself (the first
   model step's ``turn_start`` to ``MemoryInjected``, the target's measure) and the whole
   wait before the model is called (``run_start`` to ``MemoryInjected``: the audience
   checkpoint and the step's scheduling on a worker too, which grows when workers are
   short).
3. **capture** (``--capture-model``, which needs a real model): a session states a fact
   in plain words, with automatic capture after ``--quiet`` seconds of quiet; the time
   until the fact is in the owner's notebook is the capture lag.

Then the checks: every fact, the owner's shared one included, is in its owner's notebook
exactly once (lost, duplicated);
no notebook or injection holds another tenant's or owner's token (crossed); and the
latencies against the targets. Cells already in the results file are skipped (resume).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from client import User, payloads, runs_ended

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "memory_bench" / "results" / "results.jsonl"
TARGETS = {
    "recall_ms_p95": 150,  # recall added to a run's first model step
    "lost": 0,
    "duplicated": 0,
    "crossed": 0,
}
"""``docs/memory-gaps.md``'s targets. Capture lag's is 2 × the quiet window + 30 s."""
_WRITE = threading.Lock()


def token(run: str, owner: str, what: str) -> str:
    """A word naming exactly one fact's place (the owner's name holds its tenant and agent):
    found in another owner's notebook or injection, it crossed a boundary."""
    return f"zq{run}-{owner}-{what}".replace("_", "")


def pin(composition: dict[str, Any]) -> str:
    return f"composition:{composition['artifact_id']}@{composition['digest']}"


def turn(user: User, sid: str, text: str, n: int, timeout: float) -> list[dict[str, Any]]:
    """Send message ``n`` of the session and return the events of its run."""
    r = user.say(sid, text)
    if r.status_code != 202:
        raise RuntimeError(f"message {r.status_code}: {r.text[:200]}")
    events = user.wait(sid, runs_ended(n), timeout=timeout)
    starts = [i for i, e in enumerate(events) if e.get("type") == "run_start"]
    return events[starts[n - 1] :] if len(starts) >= n else events


def session(user: User, composition: dict[str, Any], model: str | None = None, **memory: Any) -> str:
    extra: dict[str, Any] = {"memory": memory} if memory else {}
    if model:
        extra["model"] = model
    r = user.create(composition["artifact_id"], composition["digest"], **extra)
    if r.status_code != 201:
        raise RuntimeError(f"create {r.status_code}: {r.text[:200]}")
    return str(r.json()["session_id"])


def notebook_facts(user: User, composition: dict[str, Any]) -> list[str]:
    """Every fact text in the owner's notebooks for this agent."""
    texts: list[str] = []
    for nb in _ok(user.get("/v1/memory/notebooks", params={"agent_pin": pin(composition)})):
        texts += [str(f.get("text", "")) for f in _ok(user.get(f"/v1/memory/notebooks/{nb['id']}/facts"))]
    return texts


def _ok(response: Any) -> Any:
    """The JSON of a successful answer; a refusal or an outage raises with what it said."""
    if response.status_code != 200:
        raise RuntimeError(f"{response.request.url.path} {response.status_code}: {response.text[:200]}")
    return response.json()


def since(run: list[dict[str, Any]], start: str, end: str = "memory_injected") -> int | None:
    """Milliseconds from the run's first ``start`` event to its first ``end`` event."""
    began = next((e["ts_ms"] for e in run if e.get("type") == start), None)
    shown = next((e["ts_ms"] for e in run if e.get("type") == end), None)
    return shown - began if began is not None and shown is not None else None


def cell(args: argparse.Namespace, run_id: str, tenant: str, agent: str, composition: dict[str, Any], s: int) -> dict[str, Any]:
    """One session's whole script: what ``report`` checks."""
    owner = f"{tenant}-{agent}-u{s // args.per_owner:03d}"
    user = User(tenant, owner)
    mine = [token(run_id, owner, f"s{s}f{i}") for i in range(args.facts)]
    shared = token(run_id, owner, "shared")
    out: dict[str, Any] = {"tenant": tenant, "agent": agent, "session": s, "owner": owner, "error": None, "mine": mine,
                           "shared": shared}
    try:
        sid = session(user, composition)
        facts = [{"text": f"The locker word is {t}."} for t in [*mine, shared]]
        run = turn(user, sid, "/tool memory " + json.dumps({"action": "add", "facts": facts}), 1, args.timeout)
        ends = payloads(run, "ToolEnd")
        out["write_ok"] = bool(ends) and (ends[0].get("result") or {}).get("ok") is True

        later = session(user, composition)
        run = turn(user, later, "What are my locker words?", 1, args.timeout)
        out["recall_ms"] = since(run, "turn_start")  # the recall step itself
        out["first_step_ms"] = since(run, "run_start")  # with the checkpoint and the step's scheduling
        out["injected"] = " ".join(str(p.get("text", "")) for p in payloads(run, "MemoryInjected"))

        if args.capture_model:
            said = token(run_id, owner, f"s{s}said")
            talker = User(tenant, owner)
            spoke = session(talker, composition, args.capture_model, capture_quiet_s=args.quiet, capture_max_turns=1)
            began = time.time()
            turn(talker, spoke, f"Please remember for later: my locker word is {said}.", 1, args.timeout)
            deadline = began + 2 * args.quiet + 120
            while time.time() < deadline:
                if any(said in f for f in notebook_facts(user, composition)):
                    out["capture_s"] = round(time.time() - began, 1)
                    break
                time.sleep(1)
            out["said"] = said
        out["notebook"] = notebook_facts(user, composition)
    except Exception as exc:  # noqa: BLE001 — a session's failure is its row, the load goes on
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def run(args: argparse.Namespace) -> None:
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str, int]] = set()
    run_id = uuid.uuid4().hex[:6]
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                run_id = row["run"]  # resume the same run: its tokens are in the notebooks
                if not row.get("error"):  # a failed session runs again
                    done.add((row["tenant"], row["agent"], int(row["session"])))
            except (ValueError, KeyError):
                continue  # a final read, or a partial line
    tenants = [f"bench{t}" for t in range(args.tenants)]
    names = args.compositions.split(",")
    compositions = {name: User(tenants[0], "bench-admin").latest("composition", name) for name in names}
    cells = [(t, a, s) for t in tenants for a in names for s in range(args.sessions)]
    todo = [c for c in cells if c not in done]
    print(f"run {run_id}: {len(cells)} sessions ({len(tenants)} tenants x {len(names)} agents x {args.sessions}), "
          f"{len(cells) - len(todo)} already done", flush=True)
    began = time.time()

    def one(c: tuple[str, str, int]) -> None:
        row = cell(args, run_id, c[0], c[1], compositions[c[1]], c[2]) | {"run": run_id}
        with _WRITE, results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if row["error"]:
            print(f"  {c[0]}/{c[1]}/{c[2]}: {row['error'][:160]}", flush=True)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        list(pool.map(one, todo))
    # Once every session is done: each owner's notebook as it finally is (a session that
    # ended early read it before its owner's other sessions wrote).
    owners = sorted({(t, a, f"{t}-{a}-u{s // args.per_owner:03d}") for t, a, s in cells})
    with results.open("a", encoding="utf-8") as f:
        for t, a, owner in owners:
            try:
                final: dict[str, Any] = {"notebook": notebook_facts(User(t, owner), compositions[a])}
            except Exception as exc:  # noqa: BLE001 — reported; the scorecard then uses the sessions' reads
                final = {"error": f"{type(exc).__name__}: {exc}"}
            f.write(json.dumps({"run": run_id, "final": owner, "tenant": t, "agent": a} | final) + "\n")
    print(f"done in {time.time() - began:.0f}s", flush=True)
    report([str(results)], quiet_s=args.quiet)


def _rows(path: Path) -> list[dict[str, Any]]:
    """The results file's rows; a line cut off by a crash (or a full disk) is skipped."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else float("nan")


def report(paths: list[str], quiet_s: float = 5.0) -> bool:
    """The scorecard; whether every target holds."""
    every = [row for p in paths for row in _rows(Path(p))]
    rows = [r for r in every if "final" not in r]
    finals = {r["final"]: r["notebook"] for r in every if "final" in r and "notebook" in r}  # the latest read wins
    if not rows:
        print("no rows")
        return False
    lost = duplicated = crossed = 0
    owner_of = {t: r["owner"] for r in rows for t in [*r.get("mine", []), r.get("shared", "")] if t}
    latest: dict[str, list[str]] = dict(finals)  # each owner's notebook once every session ended
    for r in rows:
        if not r.get("error"):
            latest.setdefault(r["owner"], r.get("notebook", []))
            seen = " ".join(r.get("notebook", [])) + " " + r.get("injected", "")
            crossed += sum(1 for t, owner in owner_of.items() if owner != r["owner"] and t in seen)
    for r in rows:
        if r.get("error") or r["owner"] not in latest:
            continue
        for t in r.get("mine", []):
            n = sum(t in f for f in latest[r["owner"]])
            lost += n == 0
            duplicated += n > 1
    for owner, texts in latest.items():  # the fact all of an owner's sessions added, once
        shared = next((r["shared"] for r in rows if r["owner"] == owner), "")
        n = sum(shared in f for f in texts)
        lost += n == 0
        duplicated += n > 1
    recalls = [float(r["recall_ms"]) for r in rows if r.get("recall_ms") is not None]
    firsts = [float(r["first_step_ms"]) for r in rows if r.get("first_step_ms") is not None]
    captures = [float(r["capture_s"]) for r in rows if r.get("capture_s") is not None]
    asked = [r for r in rows if r.get("said")]
    errors = [r for r in rows if r.get("error")]
    score = {
        "sessions": len(rows),
        "errors": len(errors),
        "lost": lost,
        "duplicated": duplicated,
        "crossed": crossed,
        "recall_ms_p50": pct(recalls, 0.5),
        "recall_ms_p95": pct(recalls, 0.95),
        "first_step_ms_p50": pct(firsts, 0.5),
        "first_step_ms_p95": pct(firsts, 0.95),
        "capture_s_p50": pct(captures, 0.5) if asked else None,
        "capture_s_p95": pct(captures, 0.95) if asked else None,
        "captured": f"{len(captures)}/{len(asked)}" if asked else None,
    }
    capture_target = 2 * quiet_s + 30
    checks = {
        "no session errors": not errors,
        **{f"{k} <= {v}": (score[k] <= v) for k, v in TARGETS.items() if not (isinstance(score[k], float) and math.isnan(score[k]))},
        **({f"capture_s_p95 <= {capture_target:g}": score["capture_s_p95"] <= capture_target,
            "every stated fact captured": len(captures) == len(asked)} if asked else {}),
    }
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
    show = sub.add_parser("report")
    show.add_argument("paths", nargs="*", default=[str(RESULTS)])
    show.add_argument("--quiet", type=float, default=5.0)
    args = parser.parse_args()
    if args.command == "run":
        run(args)
        return 0
    return 0 if report(args.paths, quiet_s=args.quiet) else 1


if __name__ == "__main__":
    sys.exit(main())
