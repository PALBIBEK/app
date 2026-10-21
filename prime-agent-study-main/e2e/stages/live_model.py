"""Live — the supervisor driven end to end by a real model, as a user would.

Not part of ``run.sh``: it needs a gateway route to a real model. Point it at one:

    LIVE_ROUTE=or-free LIVE_SMALL_ROUTE=or-free-small $PY live_model.py

A real model is not scripted, so the checks ask for what the user asked for
(the file exists and holds the answer, the fact comes back, the run ends) rather
than for an exact sequence of calls. Each check prints its evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from client import User, ctl, payloads, runs_ended, types

ROUTE = os.environ.get("LIVE_ROUTE", "or-free")
SMALL = os.environ.get("LIVE_SMALL_ROUTE", "")
HARNESS = Path(os.environ["REPO"]) / "harness"
IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
TAG = uuid.uuid4().hex[:6]
alice, bob = User("t1", "alice"), User("t2", "bob")
results: list[tuple[str, bool]] = []


def check(name: str, ok: object, detail: object = "") -> bool:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:400]}" if detail != "" else ""), flush=True)
    return bool(ok)


def info(text: str) -> None:
    print(f"     {text[:600]}", flush=True)


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


def publish(kind: str, body: dict, user: User = alice) -> dict:
    r = user.publish(kind, body)
    assert r.status_code == 200, (kind, body.get("name"), r.status_code, r.text)
    return r.json()


def ref(resp: dict) -> dict:
    return {"id": resp["artifact_id"], "digest": resp["digest"]}


def sandbox(name: str, approval: str = "never", effects: str = "workspace_write") -> dict:
    return ref(publish("sandbox_profile", COMMON | {
        "name": f"{name}-{TAG}", "provider": "local_process", "image_digest": IMAGE,
        "file_effects": effects, "approval_default": approval}))


TOOLS = local("tool_sets", "githi-tools")["definitions"] | {"bash": local("tool_sets", "coding-tools")["definitions"]["bash"]}
TOOLSET = ref(publish("tool_set", COMMON | {"name": f"live-tools-{TAG}", "definitions": TOOLS}))
DEFAULT_SANDBOX = sandbox("live-sb")
# A local_process sandbox runs commands on this host, so workspace_write refuses shell
# tools there (they need an isolating sandbox, e.g. Docker); bash runs under full access.
SHELL_SANDBOX = sandbox("live-shell", effects="danger_full_access")


def composition(name: str, route: str = ROUTE, **change: object) -> tuple[str, str]:
    body = local("compositions", "githi-v1") | {
        "name": f"{name}-{TAG}", "sandbox_profile": DEFAULT_SANDBOX, "tool_set": TOOLSET,
        "plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": route},
    } | change
    return body["name"], publish("composition", body)["digest"]


def session(comp: tuple[str, str], user: User = alice, harness: str = "githi-v1", **extra: object) -> str:
    r = user.create(comp[0], comp[1], harness=harness, **extra)
    assert r.status_code == 201, (r.status_code, r.text)
    return r.json()["session_id"]


def run_of(evs: list[dict], n: int) -> list[dict]:
    starts = [i for i, t in enumerate(types(evs)) if t == "run_start"]
    return evs[starts[n - 1]:] if len(starts) >= n else []


def turn(sid: str, text: str, n: int, timeout: float = 240, user: User = alice) -> list[dict]:
    t0 = time.time()
    r = user.say(sid, text)
    assert r.status_code == 202, (r.status_code, r.text)
    run = run_of(user.wait(sid, runs_ended(n), timeout=timeout), n)
    calls = [(p["tool_name"], p["args"]) for p in payloads(run, "ToolStart")]
    info(f"turn {n} took {time.time() - t0:.1f}s; tool calls: {json.dumps(calls)[:400]}")
    info(f"reply: {reply(run)[:300]!r}")
    errs = [e.get("payload", e) for e in run if "error" in e.get("type", "")]
    errs += [{"tool": p["tool_name"], "code": p["result"].get("code"), "error": p["result"].get("error")}
             for p in payloads(run, "ToolEnd") if p["is_error"]]
    if errs:
        info(f"errors: {json.dumps(errs)[:500]}")
    return run


def reply(run: list[dict]) -> str:
    finals = [p for p in payloads(run, "MessageEnd") if p["role"] == "assistant"]
    return " ".join(c.get("text", "") for c in finals[-1]["content"] if c.get("type") == "text") if finals else ""


def invoke(sid: str, name: str, **args: object) -> dict:
    r = alice.post(f"/v1/sessions/{sid}/tools/invoke", {"name": name, "arguments": args})
    return {"status": r.status_code} | (r.json() if r.headers.get("content-type", "").startswith("application/json") else {})


def tool_ok(run: list[dict], name: str) -> list[dict]:
    return [p for p in payloads(run, "ToolEnd") if p["tool_name"] == name and not p["is_error"]]


ONLY = set(filter(None, os.environ.get("LIVE_SECTIONS", "").upper().split(",")))


def want(letter: str, title: str) -> bool:
    """Run section ``letter`` unless LIVE_SECTIONS names others."""
    if ONLY and letter not in ONLY:
        return False
    print(f"\n## {letter}. {title}", flush=True)
    return True


def calls_ok(run: list[dict], name: str) -> list[dict]:
    """The arguments of each successful call of ``name`` in the run."""
    ok = {p["tool_call_id"] for p in tool_ok(run, name)}
    return [p["args"] for p in payloads(run, "ToolStart") if p["tool_name"] == name and p["tool_call_id"] in ok]


# --- A. Conversation -------------------------------------------------------------------
main = composition("live")
shell = composition("live-shell", sandbox_profile=SHELL_SANDBOX)
sid = session(main)
if want("A", f"A plain answer and a conversation that remembers ({ROUTE})"):
    run = turn(sid, "What is 17 multiplied by 23? Reply with just the number.", 1)
    check("the model answers through the gateway", "391" in reply(run), reply(run))
    usage = [p.get("usage") for p in payloads(run, "MessageEnd") if p["role"] == "assistant"]
    check("each model call's usage is journalled", usage and all(u and u.get("input_tokens") for u in usage), usage[-1:])

    run = turn(sid, "Create the file notes/plan.md containing a three-item markdown checklist for launching a small "
                    "website. Then read the file back and tell me its first item.", 2)
    check("the model wrote and read the file with tools", tool_ok(run, "write") and tool_ok(run, "read"),
          [p["tool_name"] for p in payloads(run, "ToolEnd")])
    plan = invoke(sid, "read", file_path="notes/plan.md")
    check("the file is in the session's workspace, as a checklist", "- [" in str(plan.get("payload")), plan)

    run = turn(sid, "Without using any tools: what was the name of the file you created in this conversation?", 3)
    check("the session carries the conversation across turns", "plan.md" in reply(run), reply(run))
    check("and answered without tools, as asked", not payloads(run, "ToolStart"),
          [p["tool_name"] for p in payloads(run, "ToolStart")])

if want("B", "Running code with bash"):
    bsid = session(shell)
    run = turn(bsid, "Write a Python script fib.py that prints the first 10 Fibonacci numbers starting 0 1, one per "
                     "line. Run it with bash (python3 fib.py) and tell me the last number it printed.", 1)
    outs = json.dumps([p["result"] for p in tool_ok(run, "bash")])
    check("the model ran its script with bash", "34" in outs, outs[:300])
    check("and reports what it printed", "34" in reply(run), reply(run))
    wsid = session(main)
    run = turn(wsid, "Run `echo hi` with the bash tool and tell me what happened.", 1)
    denied = [p for p in payloads(run, "ToolEnd") if p["tool_name"] == "bash" and p["result"].get("code") == "FILE_EFFECTS_DENIED"]
    check("on a host sandbox without full access, bash is refused (and the model is told)", denied,
          [(p["tool_name"], p["result"].get("code")) for p in payloads(run, "ToolEnd")])

if want("C", "Another tenant sees nothing"):
    r = bob.get(f"/v1/sessions/{sid}")
    check("another tenant cannot read the session", r.status_code == 404, r.status_code)
    r = bob.say(sid, "hello")
    check("nor send it a message", r.status_code in (403, 404), r.status_code)
    r = bob.get(f"/v1/sessions/{sid}/events")
    check("nor read its events", r.status_code == 404, r.status_code)

if want("D", "Subagents: one in the foreground, one in the background"):
    dsid = session(main)
    run = turn(dsid, "Use the subagent tool (not in the background) to ask a subagent for the capital of Australia, "
                     "then tell me what it said.", 1)
    ends = payloads(run, "SubagentEnd")
    check("a subagent ran and finished", ends and ends[0]["stop_reason"] == "ok", ends)
    check("its answer reaches the user", "canberra" in reply(run).lower(), reply(run))
    run = turn(dsid, "Start a background subagent (background: true) that answers: what is the chemical symbol for "
                     "gold? Then just tell me you started it; don't wait for it.", 2)
    starts = [p for p in payloads(run, "SubagentStart") if p["mode"] == "background"]
    if check("a background job was started", starts, payloads(run, "SubagentStart")):
        run_id = starts[0]["run_id"]
        evs = alice.wait(dsid, lambda e: any(p["run_id"] == run_id for p in payloads(e, "SubagentEnd")), timeout=240)
        (end,) = [p for p in payloads(evs, "SubagentEnd") if p["run_id"] == run_id]
        check("the background job finished on its own", "au" in (end.get("output") or "").lower(), end)
        run = turn(dsid, "Did your background subagent finish? What did it find?", 3)
        check("the model learns the background result", "au" in reply(run).lower(), reply(run))

if want("E", "Many hands: a second sandbox named build"):
    hsid = session(composition("live-hands", hands={"build": sandbox("live-build")}))
    run = turn(hsid, "You have two sandboxes: the default one and one called build. Write the file artifact.txt "
                     "containing 'built' in the build sandbox only (pass hand: \"build\").", 1)
    check("the model chose the build hand", any(a.get("hand") == "build" for a in calls_ok(run, "write")),
          calls_ok(run, "write"))
    r_build = invoke(hsid, "read", file_path="artifact.txt", hand="build")
    r_default = invoke(hsid, "read", file_path="artifact.txt")
    check("the file is in the build hand and not the default one",
          r_build.get("payload") == "built" and r_default.get("payload") != "built", (r_build, r_default))

if want("F", "Approvals: a write waits for the user"):
    osid = session(composition("live-ops", harness_version="githi-ops-v1"), harness="githi-ops-v1")
    alice.say(osid, "Write the file approved.txt containing the word yes.")
    evs = alice.wait(osid, lambda e: payloads(e, "ApprovalRequired") or "run_end" in types(e), timeout=180)
    asks = payloads(evs, "ApprovalRequired")
    if check("the write asks for approval", asks, types(evs)[-8:]):
        info(f"asked: {asks[0]['prompt'][:200]!r}")
        r = alice.post(f"/v1/approvals/{asks[0]['approval_id']}", {"choice": "allow"})
        check("the user approves it", r.status_code == 200, r.status_code)
        run = run_of(alice.wait(osid, runs_ended(1), timeout=180), 1)
        info(f"reply: {reply(run)[:200]!r}")
        got = invoke(osid, "read", file_path="approved.txt").get("payload") or ""
        check("and the write happens", tool_ok(run, "write") and got.strip() == "yes", got)

if want("G", "Long-term memory across sessions"):
    durable = ref(publish("memory_profile", local("memory_profiles", "default")
                          | {"name": f"live-mem-{TAG}", "recall": {"enabled": True, "backend": "durable"}}))
    mem = composition("live-mem", memory_profile=durable)
    pet = f"Zorblax-{TAG}"
    run = turn(session(mem), f"Please remember this about me for future conversations: my cat is called {pet}.", 1)
    stored = tool_ok(run, "remember") + tool_ok(run, "memory")
    check("the model stores the fact (remember or memory tool)", stored, [p["tool_name"] for p in payloads(run, "ToolEnd")])
    time.sleep(2)
    run = turn(session(mem), "What is my cat called? Answer in one line.", 1)
    check("a brand-new session recalls it", pet.lower() in reply(run).lower(), reply(run))

if want("H", "The worker dies mid-turn; the turn finishes anyway"):
    ksid = session(shell)
    alice.say(ksid, "Do these one at a time, each with its own tool call: write a.txt containing 1, write b.txt "
                    "containing 2, write c.txt containing 3, then use bash to run `cat a.txt b.txt c.txt` and tell me "
                    "the output.")
    alice.wait(ksid, lambda e: payloads(e, "ToolEnd"), timeout=180)
    ctl("kill", "worker")
    info("worker killed after the first tool call; starting it again")
    ctl("start", "worker")
    run = run_of(alice.wait(ksid, runs_ended(1), timeout=300), 1)
    info(f"reply: {reply(run)[:200]!r}; tools: {[(p['tool_name'], p['is_error']) for p in payloads(run, 'ToolEnd')]}")
    errs = [e for e in run if e.get("type") == "error"]
    if errs:
        info(f"error events: {json.dumps(errs)[:600]}")
    check("the turn ends after the restart", "run_end" in types(run), types(run)[-6:])
    files = [invoke(ksid, "read", file_path=f).get("payload") for f in ("a.txt", "b.txt", "c.txt")]
    check("every file was written", [str(f).strip() for f in files] == ["1", "2", "3"], files)
    writes = [a.get("file_path") for a in calls_ok(run, "write")]
    check("no write was carried out twice", len(writes) == len(set(writes)), writes)
    check("the model gets to its answer", "1" in reply(run) and "3" in reply(run), reply(run))

if SMALL and want("I", f"Compaction on a small window ({SMALL})"):
    csid = session(composition("live-small", route=SMALL))
    code = f"K{TAG.upper()}"
    turn(csid, f"Remember this code word for later in this conversation: {code}. Reply only 'ok'.", 1)
    filler = " ".join(f"Paragraph {i}: " + "the quick brown fox jumps over the lazy dog " * 30 for i in range(4))
    n = 1
    for _ in range(9):
        n += 1
        turn(csid, f"Summarise this in one sentence: {filler}", n)
        if payloads(alice.events(csid), "CompactionEnd"):
            break
    comps = payloads(alice.events(csid), "CompactionEnd")
    check("the session was compacted when the window filled", comps and comps[-1]["stop_reason"] == "ok", comps[-1:])
    n += 1
    run = turn(csid, "What was the code word I gave you at the start? If you no longer see it, use your history tool.", n)
    check("the model still finds the code word after compaction", code in reply(run), reply(run))

# --- Totals ----------------------------------------------------------------------------
print("\n## Totals", flush=True)
detail = alice.get(f"/v1/sessions/{sid}").json()
info(f"main session: status={detail.get('status')} {json.dumps({k: v for k, v in detail.items() if 'usage' in k or 'cost' in k})[:300]}")
passed = sum(ok for _, ok in results)
print(f"\n{passed}/{len(results)} checks passed")
for name, ok in results:
    if not ok:
        print(f"  FAILED: {name}")
sys.exit(0 if passed == len(results) else 1)
