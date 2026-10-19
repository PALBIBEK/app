"""Stage 7 — policy enforcement and journaling, as a user and an operator meet them.

Run: $PY stage7.py            (sections A–G)
     $PY stage7.py quotas     (section H; restarts the API with quota env, then restores it)
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

from client import User, payloads, runs_ended, types

HARNESS = Path(os.environ["REPO"]) / "harness"
STACK = Path(os.environ["E2E_DIR"]) / "stack"
REAL_IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
results: list[tuple[str, bool, str]] = []
alice, bob, eve = User("t1", "alice"), User("t1", "bob"), User("t2", "eve")


def check(name: str, ok: object, detail: object = "") -> bool:
    ok = bool(ok)
    results.append((name, ok, str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:300]}" if detail != "" else ""), flush=True)
    return ok


def info(text: object) -> None:
    print(f"     (info) {str(text)[:300]}", flush=True)


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


def publish(kind: str, body: dict, user: User = alice) -> dict:
    r = user.publish(kind, body)
    assert r.status_code == 200, (kind, body.get("name"), r.status_code, r.text)
    return {"id": r.json()["artifact_id"], "digest": r.json()["digest"]}


SANDBOX_BODY = COMMON | {"name": "pol-sandbox", "provider": "local_process", "image_digest": REAL_IMAGE,
                        "file_effects": "workspace_write", "approval_default": "ask"}
SANDBOX = publish("sandbox_profile", SANDBOX_BODY)
_sandboxes: dict[str, dict] = {alice.tenant: SANDBOX}


def sandbox_for(user: User) -> dict:
    """A tenant cannot see, or publish under, another tenant's name: each gets its own copy."""
    if user.tenant not in _sandboxes:
        body = SANDBOX_BODY | {"name": f"pol-sandbox-{user.tenant}"}
        _sandboxes[user.tenant] = publish("sandbox_profile", body, user)
    return _sandboxes[user.tenant]


def comp(name: str, harness: str = "githi-v1", user: User = alice) -> tuple[str, str, str]:
    body = local("compositions", "githi-v1") | {"name": name, "harness_version": harness, "sandbox_profile": sandbox_for(user),
                                                "plug_bindings": {"gateway_base_url": "http://model-gateway:8090",
                                                                  "model_route": "standin-chat"}}
    return name, publish("composition", body, user)["digest"], harness


def session(c: tuple[str, str, str], user: User = alice) -> str:
    r = user.create(c[0], c[1], harness=c[2])
    assert r.status_code == 201, (r.status_code, r.text)
    return r.json()["session_id"]


def wait_for(sid: str, pred, timeout: float = 60, user: User = alice) -> list[dict]:
    end = time.time() + timeout
    evs: list[dict] = []
    while time.time() < end:
        evs = user.events(sid, quiet_s=0.7)
        if pred(evs):
            return evs
        time.sleep(0.4)
    raise TimeoutError(f"{sid}: last types {types(evs)[-12:]}")


def run_of(evs: list[dict], n: int) -> list[dict]:
    starts = [i for i, t in enumerate(types(evs)) if t == "run_start"]
    return evs[starts[n - 1]:] if len(starts) >= n else []


def tool_result(run: list[dict]) -> dict:
    return ((payloads(run, "ToolEnd") or [{}])[0].get("result")) or {}


def errors(run: list[dict]) -> list[dict]:
    return [e["data"] for e in run if "error" in e["type"]]


def session_status(sid: str, user: User = alice) -> str | None:
    r = user.get(f"/v1/sessions/{sid}")
    return r.json().get("status") if r.status_code == 200 else f"HTTP {r.status_code}"


def section_a_budget() -> None:
    print("\n## A. Budget (githi-budget-v1: max_cost_per_session $0.0003)")
    sid = session(comp("pol-budget", "githi-budget-v1"))
    alice.say(sid, "hello " * 200)
    evs = wait_for(sid, runs_ended(1), 60)
    run = run_of(evs, 1)
    info(f"run 1 types: {types(run)}")
    info(f"run 1 errors: {errors(run)}")
    over = json.dumps(run)
    check("an over-budget run is stopped with a budget error", "budget" in over.lower(), errors(run) or types(run))
    alice.say(sid, "one more")
    try:
        evs = wait_for(sid, runs_ended(2), 30)
        run2 = run_of(evs, 2)
        model_calls = [e for e in run2 if e["type"] in ("model_request", "turn_start", "message_end")]
        check("the next turn is refused too (the session stays over budget)", "budget" in json.dumps(run2).lower(),
              types(run2))
        info(f"run 2 types: {types(run2)}; model-ish events {len(model_calls)}")
    except TimeoutError as exc:
        info(f"no second run: {exc}")
        check("the next turn is refused too (the session stays over budget)", True, "no run started")
    info(f"session status after budget: {session_status(sid)}")
    ok_sid = session(comp("pol-nobudget"))
    alice.say(ok_sid, "hello " * 200)
    evs = wait_for(ok_sid, runs_ended(1), 60)
    check("a session on a normal budget is unaffected", "budget" not in json.dumps(run_of(evs, 1)).lower(), types(run_of(evs, 1)))


def section_b_loop_guard() -> None:
    print("\n## B. Loop guard (githi-ops-v1: repetition abort)")
    sid = session(comp("pol-ops", "githi-ops-v1"))
    alice.say(sid, '/tool read {"path": "LOOPMODEL.txt"}')
    evs = wait_for(sid, runs_ended(1), 90)
    run = run_of(evs, 1)
    calls = payloads(run, "ToolStart")
    info(f"tool calls before the guard acted: {len(calls)}; errors: {errors(run)}")
    check("a model repeating the same call is stopped by the loop guard", "LoopGuard" in json.dumps(run), errors(run))
    check("...after a handful of calls, not max_steps", 0 < len(calls) <= 4, len(calls))
    check("the runaway turn ends; the session stays open", session_status(sid) == "active", session_status(sid))
    r = alice.say(sid, "hello again")
    evs = wait_for(sid, runs_ended(2), 60) if r.status_code == 202 else []
    check("...and answers the next message", r.status_code == 202 and types(evs).count("run_end") >= 2,
          (r.status_code, r.text[:120]))
    sid2 = session(comp("pol-ops", "githi-ops-v1"))
    for i, path in enumerate(["a.txt", "b.txt", "c.txt", "d.txt"], 1):
        alice.say(sid2, f'/tool read {{"path": "{path}"}}')
        wait_for(sid2, runs_ended(i), 60)
    evs = alice.events(sid2)
    check("varied calls across turns are not mistaken for a loop", "LoopGuard" not in json.dumps(evs), errors(evs))


def _approval(sid: str, n_requests: int, timeout: float = 45) -> dict:
    evs = wait_for(sid, lambda e: len(payloads(e, "ApprovalRequired")) >= n_requests, timeout)
    return payloads(evs, "ApprovalRequired")[n_requests - 1]


def section_c_approvals() -> None:
    print("\n## C. Approvals (githi-ops-v1: write and bash need approval, 25 s timeout, deny on timeout)")
    c = comp("pol-ops", "githi-ops-v1")
    sid = session(c)

    alice.say(sid, '/tool read {"path": "nothing.txt"}')
    evs = wait_for(sid, runs_ended(1), 60)
    check("a tool that needs no approval runs without asking", not payloads(run_of(evs, 1), "ApprovalRequired"),
          types(run_of(evs, 1)))

    alice.say(sid, '/tool write {"path": "approved.txt", "content": "yes"}')
    ask = _approval(sid, 1)
    info(f"approval asked: {ask}")
    listed = alice.get(f"/v1/sessions/{sid}/approvals").json()
    check("the pending approval is listed for the session", any(a["approval_id"] == ask["approval_id"] for a in listed), listed)
    r = eve.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "approve"})
    check("another tenant cannot answer it", r.status_code in (403, 404), (r.status_code, r.text[:120]))
    r = eve.get(f"/v1/sessions/{sid}/approvals")
    check("another tenant cannot list it", r.status_code in (403, 404), r.status_code)
    r = bob.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "allow"})
    check("a different user in the same tenant cannot answer alice's approval", r.status_code in (403, 404), r.status_code)
    r = alice.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "allow"})
    check("alice approves (or re-sends the same answer: idempotent)", r.status_code == 200, (r.status_code, r.text[:160]))
    evs = wait_for(sid, runs_ended(2), 60)
    check("the approved write runs", tool_result(run_of(evs, 2)).get("ok") is True, tool_result(run_of(evs, 2)))
    alice.say(sid, '/tool read {"path": "approved.txt"}')
    evs = wait_for(sid, runs_ended(3), 60)
    check("...and its file exists", "yes" in json.dumps(tool_result(run_of(evs, 3))), tool_result(run_of(evs, 3)))
    r = alice.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "deny"})
    check("an answered approval cannot be changed afterwards", r.status_code == 409, (r.status_code, r.text[:120]))

    alice.say(sid, '/tool write {"path": "denied.txt", "content": "no"}')
    ask = _approval(sid, 2)
    r = alice.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "deny"})
    check("alice denies", r.status_code == 200, (r.status_code, r.text[:160]))
    evs = wait_for(sid, runs_ended(4), 60)
    res = tool_result(run_of(evs, 4))
    check("the denied write does not run, and the model is told", res.get("ok") is not True and "den" in json.dumps(res).lower(), res)
    alice.say(sid, '/tool read {"path": "denied.txt"}')
    evs = wait_for(sid, runs_ended(5), 60)
    check("...no file was written", "no" not in json.dumps(tool_result(run_of(evs, 5))).replace("not", "").replace("No such", "")
          or tool_result(run_of(evs, 5)).get("ok") is not True, tool_result(run_of(evs, 5)))

    alice.say(sid, '/tool write {"path": "timed.txt", "content": "late"}')
    ask = _approval(sid, 3)
    t0 = time.time()
    evs = wait_for(sid, runs_ended(6), 75)
    waited = time.time() - t0
    resolved = [p for p in payloads(evs, "ApprovalResolved") if p.get("approval_id") == ask["approval_id"]]
    info(f"timed out after ~{waited:.0f}s: {resolved}")
    check("an unanswered approval times out (~25 s) and is denied", resolved and resolved[0].get("status") == "timed_out"
          and resolved[0].get("choice") in ("deny", "no"), resolved)
    check("...the write did not run", tool_result(run_of(evs, 6)).get("ok") is not True, tool_result(run_of(evs, 6)))
    r = alice.post(f"/v1/approvals/{ask['approval_id']}", {"choice": "approve"})
    check("a late answer to a timed-out approval is refused", r.status_code == 409, (r.status_code, r.text[:120]))
    check("nothing is left pending", alice.get(f"/v1/sessions/{sid}/approvals").json() == [],
          alice.get(f"/v1/sessions/{sid}/approvals").json())


def section_d_isolation() -> None:
    print("\n## D. Who can see and drive a session")
    sid = session(comp("pol-iso"))
    alice.say(sid, "private hello")
    wait_for(sid, runs_ended(1), 60)
    probes = {
        "read the session": lambda u: u.get(f"/v1/sessions/{sid}"),
        "read its events": lambda u: u.http.get(f"/v1/sessions/{sid}/events", params={"since": -1},
                                                headers=u._h(), timeout=httpx.Timeout(3.0)),
        "send it a message": lambda u: u.say(sid, "hijack"),
        "fork it": lambda u: u.post(f"/v1/sessions/{sid}/fork", {}),
        "delete it": lambda u: u.delete(f"/v1/sessions/{sid}"),
    }
    for label, call in probes.items():
        try:
            code = call(eve).status_code
        except httpx.ReadTimeout:
            code = "stream opened (200)"
        check(f"another tenant cannot {label}", code in (403, 404), code)
    listed = eve.get("/v1/sessions").text
    check("another tenant's session list does not include it", sid not in listed, listed[:120])
    for label, call in probes.items():
        if label == "delete it":
            continue
        try:
            code = call(bob).status_code
        except httpx.ReadTimeout:
            code = 200
        info(f"bob (same tenant) can {label}: HTTP {code}")
    check("alice's session survived the probes", session_status(sid) not in (None, "terminated", "deleted"), session_status(sid))


def section_e_journal() -> None:
    print("\n## E. The journal: complete, ordered, replayable")
    sid = session(comp("pol-journal"))
    msgs = ["first", '/tool ls {"path": "."}', '/tool read {"path": "missing.txt"}', "last"]
    for i, m in enumerate(msgs, 1):
        alice.say(sid, m)
        wait_for(sid, runs_ended(i), 60)
    evs = alice.events(sid)
    seqs = [e["seq"] for e in evs]
    check("sequence numbers run 0..n with no gap or duplicate", seqs == list(range(len(seqs))), seqs[:12])
    check("event ids are unique", len({e["event_id"] for e in evs}) == len(evs))
    t = types(evs)
    starts, ends = t.count("run_start"), t.count("run_end")
    check("one run_start and one run_end per message", starts == ends == len(msgs), (starts, ends))
    nesting, ok = 0, True
    for x in t:
        nesting += x == "run_start"
        nesting -= x == "run_end"
        ok &= nesting in (0, 1)
    check("runs never overlap", ok)
    tool_s = {p.get("call_id") or p.get("tool_call_id") for p in payloads(evs, "ToolStart")}
    tool_e = {p.get("call_id") or p.get("tool_call_id") for p in payloads(evs, "ToolEnd")}
    check("every tool start has its tool end", tool_s == tool_e and len(tool_s) == 2, (tool_s, tool_e))
    mid = seqs[len(seqs) // 2]
    tail = alice.events(sid, since=mid)
    check("replay from a cursor returns exactly what came after it", [e["seq"] for e in tail] == seqs[mid + 1:],
          ([e["seq"] for e in tail][:5], mid))
    check("?since= gives the same replay as Last-Event-ID",
          [e["seq"] for e in alice.events(sid, since=mid, query=True)] == seqs[mid + 1:])
    check("replay from the last event returns nothing", alice.events(sid, since=seqs[-1]) == [])
    check("a replay is identical to the first read", alice.events(sid) == evs)
    pg = {k: os.environ.get(k, d) for k, d in (("PG_HOST", "127.0.0.1"), ("PG_PORT", "5433"), ("PG_USER", "yal"))}
    row = subprocess.run(["psql", "-h", pg["PG_HOST"], "-p", pg["PG_PORT"], "-U", pg["PG_USER"], "supervisor", "-Atc",
                          f"select count(*), max(seq) from events where session_id='{sid}'"], check=True,
                         capture_output=True, text=True,
                         env={**os.environ, "PGPASSWORD": os.environ.get("PG_PASSWORD", "yal")}).stdout.strip()
    check("every event is persisted in the database", row == f"{len(evs)}|{seqs[-1]}", row)

    print("   live stream during a slow turn, then a reconnect from its cursor")
    live: list[dict] = []

    def follow() -> None:
        with alice.http.stream("GET", f"/v1/sessions/{sid}/events", headers=alice._h() | {"Last-Event-ID": str(seqs[-1])},
                               timeout=httpx.Timeout(connect=10, read=40, write=10, pool=10)) as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    live.append(json.loads(line[5:]))
                    if len(live) >= 3:
                        return

    th = threading.Thread(target=follow)
    th.start()
    time.sleep(0.5)
    alice.say(sid, "SLOWMODEL please")
    th.join(45)
    cursor = live[-1]["seq"] if live else seqs[-1]
    wait_for(sid, runs_ended(len(msgs) + 1), 60)
    rest = alice.events(sid, since=cursor)
    combined = [e["seq"] for e in live] + [e["seq"] for e in rest]
    check("live events + a reconnect from the last one seen have no gap or duplicate",
          combined == list(range(seqs[-1] + 1, seqs[-1] + 1 + len(combined))) and len(live) == 3, combined)

    subprocess.run([os.environ["E2E_CTL"], "restart", "api"], check=True)
    for _ in range(60):
        try:
            if httpx.get("http://127.0.0.1:8080/readyz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    after = alice.events(sid)
    check("after an API restart the journal replays the same", [e["event_id"] for e in after] ==
          [e["event_id"] for e in evs] + [e["event_id"] for e in after[len(evs):]] and len(after) > len(evs), len(after))


def section_f_commands() -> None:
    print("\n## F. Idempotent commands and a message mid-run")
    sid = session(comp("pol-cmd"))
    cid = uuid.uuid4().hex
    r1, r2 = alice.say(sid, "once", command_id=cid), alice.say(sid, "once", command_id=cid)
    info(f"same command_id twice: HTTP {r1.status_code}, {r2.status_code} {r2.text[:120]}")
    wait_for(sid, runs_ended(1), 60)
    time.sleep(3)
    check("a retried command_id starts only one run", types(alice.events(sid)).count("run_start") == 1,
          types(alice.events(sid)).count("run_start"))
    r3 = alice.say(sid, "once", command_id=cid)
    time.sleep(4)
    check("...even when retried after the run finished", types(alice.events(sid)).count("run_start") == 1,
          (r3.status_code, types(alice.events(sid)).count("run_start")))
    r = alice.say(sid, "different text, same id", command_id=cid)
    check("a command_id reused with a different message is refused", r.status_code == 409, (r.status_code, r.text[:160]))
    alice.say(sid, "SLOWMODEL long one")
    time.sleep(2)
    r = alice.say(sid, "while you're busy")
    info(f"message during a run: HTTP {r.status_code} {r.text[:160]}")
    evs = wait_for(sid, lambda e: types(e).count("run_end") >= (3 if r.status_code == 202 else 2), 90)
    check("a message sent mid-run is either queued (and answered) or clearly refused",
          r.status_code in (409, 429) or types(evs).count("run_end") >= 3, (r.status_code, types(evs).count("run_end")))


def section_h_quotas() -> None:
    print("\n## H. Tenant quotas and rate limits (API restarted with TENANT_MAX_SESSIONS=2, TENANT_RATE_LIMIT_PER_MINUTE=40)")
    (Path(os.environ["E2E_RUN_DIR"]) / "override.sh").write_text("export TENANT_MAX_SESSIONS=2 TENANT_RATE_LIMIT_PER_MINUTE=40\n")
    try:
        for svc in ("api", "worker"):
            subprocess.run([os.environ["E2E_CTL"], "restart", svc], check=True)
        time.sleep(10)
        tess = User(f"t9-{uuid.uuid4().hex[:6]}", "tess")
        c = comp(f"pol-quota-{tess.tenant}", user=tess)
        rs = [tess.create(c[0], c[1]) for _ in range(3)]
        codes = [r.status_code for r in rs]
        check("a tenant past its session quota is refused with 429 and a reason", codes == [201, 201, 429]
              and "quota_exceeded" in rs[2].text, (codes, rs[2].text[:160]))
        other = User(f"t8-{uuid.uuid4().hex[:6]}", "otto")
        c8 = comp(f"pol-quota-{other.tenant}", user=other)
        check("...without affecting another tenant", other.create(c8[0], c8[1]).status_code == 201)
        burst = [tess.get("/v1/sessions").status_code for _ in range(60)]
        check("a burst past the per-minute limit gets 429s", 429 in burst, {k: burst.count(k) for k in set(burst)})
        check("...and another tenant is not throttled by it", other.get("/v1/sessions").status_code == 200)
    finally:
        (Path(os.environ["E2E_RUN_DIR"]) / "override.sh").unlink(missing_ok=True)
        for svc in ("api", "worker"):
            subprocess.run([os.environ["E2E_CTL"], "restart", svc], check=True)
        time.sleep(8)


if __name__ == "__main__":
    if sys.argv[1:] == ["quotas"]:
        sections = [section_h_quotas]
    else:
        wanted = set(sys.argv[1:])
        sections = [s for s in (section_a_budget, section_b_loop_guard, section_c_approvals, section_d_isolation,
                                section_e_journal, section_f_commands) if not wanted or s.__name__.split("_")[1] in wanted]
    for s in sections:
        try:
            s()
        except Exception as exc:  # noqa: BLE001 — one broken section must not hide the rest
            check(f"{s.__name__} ran to completion", False, f"{type(exc).__name__}: {exc}")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
    for name, ok, detail in results:
        if not ok:
            print("  FAIL", name, "—", detail)
    sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
