"""Stage 6 — session memory, as a user relies on it. Run: $PY stage6.py"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from client import User, ctl, payloads, relay_mark, relay_since, runs_ended, types

HARNESS = Path(os.environ["REPO"]) / "harness"
REAL_IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
results: list[tuple[str, bool, str]] = []
alice, bob = User("t1", "alice"), User("t1", "bob")


def check(name: str, ok: object, detail: object = "") -> bool:
    ok = bool(ok)
    results.append((name, ok, str(detail)[:280]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:280]}" if detail != "" else ""), flush=True)
    return ok


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


def publish(kind: str, body: dict) -> dict:
    r = alice.publish(kind, body)
    assert r.status_code == 200, (kind, body.get("name"), r.status_code, r.text)
    return {"id": r.json()["artifact_id"], "digest": r.json()["digest"]}


SANDBOX = publish("sandbox_profile", COMMON | {"name": "mem-sandbox", "provider": "local_process", "image_digest": REAL_IMAGE,
                                               "file_effects": "workspace_write", "approval_default": "ask"})


def comp(name: str, route: str = "standin-chat", memory_profile: dict | None | str = "default") -> tuple[str, str]:
    body = local("compositions", "githi-v1") | {"name": name, "sandbox_profile": SANDBOX,
                                                "plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": route}}
    if memory_profile is None:
        body.pop("memory_profile", None)
    elif memory_profile != "default":
        body["memory_profile"] = memory_profile
    return name, publish("composition", body)["digest"]


def session(c: tuple[str, str], user: User = alice, memory: dict | None = None) -> str:
    r = user.create(c[0], c[1], **({"memory": memory} if memory else {}))
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def turn(sid: str, text: str, n: int, user: User = alice) -> tuple[list[dict], list[dict]]:
    mark = relay_mark()
    assert user.say(sid, text).status_code == 202
    evs = user.wait(sid, runs_ended(n), timeout=90)
    starts = [i for i, t in enumerate(types(evs)) if t == "run_start"]
    return evs[starts[n - 1]:], relay_since(mark)


def seen(calls: list[dict]) -> str:
    """Everything the agent's first model call of a run contained."""
    agent = [c for c in calls if "memory" in json.dumps(c["body"].get("tools") or [])]
    return json.dumps(agent[0]["body"]) if agent else ""


def pin(c: tuple[str, str]) -> str:
    return f"composition:{c[0]}@{c[1]}"


# --- A. the conversation carries across turns and restarts ---------------------------
print("\n## A. The conversation carries across turns, and across a worker restart")
c = comp("mem-conv")
sid = session(c)
turn(sid, "My favourite colour is teal.", 1)
_, calls = turn(sid, "What did I just tell you?", 2)
check("turn 2's model call includes turn 1", "favourite colour is teal" in seen(calls), seen(calls)[:120])
ctl("restart", "worker")
_, calls = turn(sid, "And now?", 3)
check("after a worker restart the model still sees the whole conversation",
      "favourite colour is teal" in seen(calls) and "What did I just tell you" in seen(calls), seen(calls)[:120])

# --- B. compaction from the pinned memory profile ---------------------------------
print("\n## B. Compaction (memory profile + the gateway's reported window)")
c = comp("mem-compact", route="tiny-window")
sid = session(c)
long_text = "lorem ipsum dolor " * 700  # ~3k tokens; the route reports a 3,000-token window
turn(sid, long_text, 1)
run, calls = turn(sid, "Short follow-up.", 2)
end = (payloads(run, "CompactionEnd") or [{}])[0]
check("a conversation past the window is compacted before the next turn", end.get("stop_reason") == "ok", end)
body = seen(calls)
check("the next model call no longer carries the long turn", long_text.strip()[:200] not in body, len(body))
summary = next((m for m in (end.get("messages") or []) if m.get("role") == "user"), {}).get("content", "")
print(f"     (info) what replaced it: {str(summary)[:160]!r}")
check("the replacement is a real summary (not the bare fallback notice)", "[context checkpoint]" in str(summary), str(summary)[:120])
c = comp("mem-nocompact", route="tiny-window", memory_profile=publish("memory_profile", local("memory_profiles", "default")
                                                                      | {"name": "mem-off", "compaction": dict(local("memory_profiles", "default")["compaction"], enabled=False)}))
sid = session(c)
turn(sid, long_text, 1)
run, _ = turn(sid, "Short follow-up.", 2)
check("with compaction off in the profile, nothing is compacted", not payloads(run, "CompactionEnd"), types(run))

# --- C. notebook memory (QM-style) ---------------------------------------------------
print("\n## C. Notebook memory: facts, recall, the memory and history tools")
c = comp("mem-notebook")
r = alice.post("/v1/memory/notebooks", {"agent_pin": pin(c), "name": "default", "label": "Alice's notes"})
check("alice creates her notebook for this agent", r.status_code in (201, 409), (r.status_code, r.text[:200]))
nbs = alice.get("/v1/memory/notebooks", params={"agent_pin": pin(c)}).json()
nb = next((n for n in nbs if n["name"] == "default"), None)
check("the notebook is listed", nb is not None, nbs)
if nb:
    r = alice.post(f"/v1/memory/notebooks/{nb['id']}/facts", {"facts": ["The project codename is Falcon."]})
    check("alice adds a fact through the API", r.status_code == 201, (r.status_code, r.text[:160]))
sid = session(c)
run, calls = turn(sid, "Remind me of the codename.", 1)
injected = (payloads(run, "MemoryInjected") or [{}])[0]
check("the fact is recalled into the turn (MemoryInjected)", "Falcon" in json.dumps(injected), injected)
check("...and the model actually received it", "Falcon" in seen(calls), seen(calls)[:120])
run, _ = turn(sid, '/tool memory {"action": "add", "facts": [{"text": "Alice prefers tea."}]}', 2)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("the model adds a fact with the memory tool", (end.get("result") or {}).get("ok") is True, end.get("result"))
facts = alice.get(f"/v1/memory/notebooks/{nb['id']}/facts").json() if nb else []
check("the model's fact is visible to alice in her notebook", any("tea" in json.dumps(f) for f in facts), facts)
run, _ = turn(sid, '/tool memory {"action": "search", "query": "tea"}', 3)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("the model finds it again with search", "tea" in json.dumps(end.get("result")), end.get("result"))
run, _ = turn(sid, '/tool history {"query": "codename"}', 4)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("the history tool finds earlier turns of the conversation", "codename" in json.dumps(end.get("result")), str(end.get("result"))[:200])

sid_b = session(c, user=bob)
run, calls = turn(sid_b, "What is the codename?", 1, user=bob)
check("bob's session on the same agent does not see alice's facts", "Falcon" not in seen(calls), seen(calls)[:120])
r = bob.get(f"/v1/memory/notebooks/{nb['id']}/facts") if nb else None
check("bob cannot read alice's notebook through the API", r is not None and r.status_code in (403, 404), r.status_code if r else None)

# --- D. long-term memory through the memory service ---------------------------------
print("\n## D. Long-term memory service (remember + durable recall)")
durable = publish("memory_profile", local("memory_profiles", "default") | {"name": "mem-durable", "recall": {"enabled": True, "backend": "durable"}})
c = comp("mem-ltm", memory_profile=durable)
s1 = session(c)
run, _ = turn(s1, '/tool remember {"content": "Alice dog is named Rex"}', 1)
check("remember stores the fact", ((payloads(run, "ToolEnd") or [{}])[0].get("result") or {}).get("ok") is True)
run, calls = turn(s1, "Alice dog named", 2)
rec = (payloads(run, "MemoryRecall") or [{}])[0]
check("the same session recalls it on a later turn", "Rex" in json.dumps(rec) or "Rex" in seen(calls), rec)
s2 = session(c)
run, calls = turn(s2, "Alice dog named", 1)
rec = (payloads(run, "MemoryRecall") or [{}])[0]
check("a NEW session for the same user recalls it (long-term memory)", "Rex" in json.dumps(rec) or "Rex" in seen(calls), rec)
s3 = session(c, user=bob)
run, calls = turn(s3, "Alice dog named", 1, user=bob)
rec = (payloads(run, "MemoryRecall") or [{}])[0]
check("another user's session on the same agent does not recall it", "Rex" not in json.dumps(rec) and "Rex" not in seen(calls), rec)

# --- E. fork carries the conversation --------------------------------------------
print("\n## E. Fork")
c = comp("mem-fork")
sid = session(c)
turn(sid, "The secret word is pineapple.", 1)
r = alice.post(f"/v1/sessions/{sid}/fork", {})
check("fork a session", r.status_code == 201, (r.status_code, r.text[:160]))
if r.status_code == 201:
    fid = r.json()["session_id"]
    _, calls = turn(fid, "What is the secret word?", 2 if types(alice.events(fid)).count("run_end") else 1)
    check("the fork's model call carries the original conversation", "pineapple" in seen(calls), seen(calls)[:120])

print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
for name, ok, detail in results:
    if not ok:
        print("  FAIL", name, "—", detail)

sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
