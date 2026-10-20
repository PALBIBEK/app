"""Stage 4 — sandbox management as a user meets it. Run: $PY stage4.py"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from client import User, ctl, eventually, payloads, runs_ended, types

HARNESS = Path(os.environ["REPO"]) / "harness"
REAL_IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
results: list[tuple[str, bool, str]] = []
alice, bob = User("t1", "alice"), User("t1", "bob")


def check(name: str, ok: object, detail: object = "") -> bool:
    ok = bool(ok)
    results.append((name, ok, str(detail)[:260]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:260]}" if detail != "" else ""))
    return ok


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


def publish(kind: str, body: dict) -> dict:
    r = alice.publish(kind, body)
    assert r.status_code == 200, (kind, body.get("name"), r.status_code, r.text)
    return {"id": r.json()["artifact_id"], "digest": r.json()["digest"]}


def profile(name: str, effects: str) -> dict:
    return publish("sandbox_profile", COMMON | {"name": name, "provider": "local_process", "image_digest": REAL_IMAGE,
                                                "file_effects": effects, "approval_default": "ask"})


TOOLS = local("tool_sets", "githi-tools")["definitions"] | local("tool_sets", "coding-tools")["definitions"]
TOOLS["edit"] = {"name": "edit", "description": "Replace exact text in a file", "tool_class": "idempotent",
                 "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}, "old_text": {"type": "string"},
                                                                 "new_text": {"type": "string"}}, "required": ["file_path", "old_text", "new_text"]}}
ALL_TOOLS = publish("tool_set", {"name": "all-tools", **COMMON, "definitions": TOOLS})


def composition(name: str, sandbox: dict) -> tuple[str, str]:
    body = local("compositions", "githi-v1") | {"name": name, "sandbox_profile": sandbox, "tool_set": ALL_TOOLS,
                                                "plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "standin-chat"}}
    return name, publish("composition", body)["digest"]


def session(comp: tuple[str, str], user: User = alice, scope: str | None = None) -> tuple[int, dict]:
    extra = {"scope_id": scope} if scope else {}
    r = user.create(comp[0], comp[1], **extra)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else {"raw": r.text})


def invoke(sid: str, name: str, user: User = alice, **args: object) -> dict:
    r = user.post(f"/v1/sessions/{sid}/tools/invoke", {"name": name, "arguments": args})
    try:
        return {"status": r.status_code} | r.json()
    except ValueError:
        return {"status": r.status_code, "raw": r.text}


def turn(sid: str, text: str, n: int, user: User = alice) -> list[dict]:
    assert user.say(sid, text).status_code == 202
    evs = user.wait(sid, runs_ended(n), timeout=90)
    starts = [i for i, t in enumerate(types(evs)) if t == "run_start"]
    return evs[starts[n - 1]:]


# --- A. file effects --------------------------------------------------------------
print("\n## A. File-effect policies (direct invoke and model turn)")
ro = composition("fx-readonly", profile("fx-readonly", "read_only"))
ww = composition("fx-write", profile("fx-write", "workspace_write"))
full = composition("fx-full", profile("fx-full", "danger_full_access"))

_, s = session(ro); sid = s["session_id"]
r = invoke(sid, "write", file_path="a.txt", content="x")
check("[read_only] write refused", r.get("code") == "FILE_EFFECTS_DENIED", r)
check("[read_only] ls allowed", invoke(sid, "ls", path=".").get("ok") is True)
run = turn(sid, '/tool write {"file_path": "a.txt", "content": "x"}', 1)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("[read_only] write refused in a model turn too", (end.get("result") or {}).get("code") == "FILE_EFFECTS_DENIED", end.get("result"))

_, s = session(ww); sid = s["session_id"]
check("[workspace_write] write allowed", invoke(sid, "write", file_path="a.txt", content="one two").get("ok") is True)
r = invoke(sid, "edit", file_path="a.txt", old_text="two", new_text="three")
check("[workspace_write] edit allowed", r.get("ok") is True, r)
check("[workspace_write] bash refused", invoke(sid, "bash", command="true").get("code") == "FILE_EFFECTS_DENIED")

_, s = session(full); full_sid = s["session_id"]
check("[full] bash allowed", invoke(full_sid, "bash", command="echo ok").get("payload", {}).get("stdout") == "ok\n")

# --- B. path confinement ---------------------------------------------------------
print("\n## B. Paths stay inside the workspace")
for path in ("../escape.txt", "/etc/passwd", "a/../../b.txt"):
    r = invoke(full_sid, "read", file_path=path)
    check(f"read {path!r} refused", r.get("code") == "PATH_ESCAPE", r.get("code"))
r = invoke(full_sid, "write", file_path="../escape.txt", content="x")
check("write ../escape.txt refused", r.get("code") == "PATH_ESCAPE", r.get("code"))
invoke(full_sid, "bash", command="ln -sf /etc/passwd link")
r = invoke(full_sid, "read", file_path="link")
check("read through a symlink to /etc/passwd refused as PATH_ESCAPE", r.get("code") == "PATH_ESCAPE", r)
r = invoke(full_sid, "bash", command=f"cat {os.environ['E2E_DIR']}/stack/env.sh | head -c 60; echo; ls /tmp/yal-local-sandboxes | wc -l")
print("     (info) bash in local_process sees the host:", json.dumps(r.get("payload", {}).get("stdout", ""))[:160])

# --- C. one sandbox per session ---------------------------------------------------
print("\n## C. Each session has its own sandbox; delete cleans up")
_, s1 = session(full); _, s2 = session(full)
invoke(s1["session_id"], "write", file_path="private.txt", content="s1 only")
r = invoke(s2["session_id"], "read", file_path="private.txt")
check("another session cannot read my files through tools", r.get("code") == "FILE_NOT_FOUND", r.get("code"))
where = invoke(s1["session_id"], "bash", command="pwd").get("payload", {}).get("stdout", "").strip()
r = alice.delete(f"/v1/sessions/{s1['session_id']}")
try:
    eventually(lambda: not Path(where).exists(), 30, what="the sandbox directory to go")
except TimeoutError:
    pass  # reported by the check below
check("deleting a session destroys its sandbox", r.status_code == 204 and not Path(where).exists(), (r.status_code, where, Path(where).exists()))

# --- D. workspace scopes -----------------------------------------------------------
print("\n## D. Shared, durable workspaces (scopes)")
scope = f"proj-{uuid.uuid4().hex[:6]}"
r = alice.post(f"/v1/scopes/{scope}/ensure")
check("alice creates a scope", r.status_code == 200, (r.status_code, r.text[:160]))
r = alice.http.put(f"/v1/scopes/{scope}/files/docs/brief.md", content=b"# Brief\nship it\n", headers=alice._h() | {"content-type": "application/octet-stream"})
check("alice uploads a file to the scope", r.status_code == 200, (r.status_code, r.text[:200]))
code, s = session(full, scope=scope)
check("session bound to the scope starts", code == 201, (code, s))
sid = s.get("session_id")
if sid:
    r = invoke(sid, "read", file_path="docs/brief.md")
    check("agent sees the uploaded file in its sandbox", r.get("payload") == "# Brief\nship it\n", r)
    run = turn(sid, '/tool write {"file_path": "out/report.txt", "content": "done"}', 1)

    def scope_files() -> list | dict:
        return alice.get(f"/v1/scopes/{scope}/files").json()

    try:
        eventually(lambda: "out/report.txt" in str(scope_files()), 30, what="the sync back")
    except TimeoutError:
        pass  # reported by the check below
    files = scope_files()
    paths = sorted(f.get("path") for f in files) if isinstance(files, list) else files
    check("what the agent wrote is synced back after the run", isinstance(paths, list) and "out/report.txt" in paths, paths)
    code, s2 = session(full, scope=scope)
    r = invoke(s2["session_id"], "read", file_path="out/report.txt") if code == 201 else {}
    check("a second session on the scope sees the first one's output", r.get("payload") == "done", (code, r))

code, s = session(full, user=bob, scope=scope)
check("bob (not a member) cannot start a session on alice's scope", code in (403, 404), (code, s))
r = bob.get(f"/v1/scopes/{scope}/files")
check("bob cannot list alice's scope files", r.status_code in (403, 404), r.status_code)
r = alice.post(f"/v1/scopes/{scope}/members", {"principal_id": "bob"})
check("alice adds bob as a member", r.status_code in (200, 201), (r.status_code, r.text[:160]))
code, s = session(full, user=bob, scope=scope)
check("bob can now start a session on the scope", code == 201, (code, s))

# concurrent edits of the same file from two sessions (F20): no last-writer-wins
code, a = session(full, scope=scope); code2, b = session(full, scope=scope)
check("two sessions on one scope start", code == code2 == 201, (code, code2))
if code == 201 and code2 == 201:
    turn(a["session_id"], '/tool write {"file_path": "shared.txt", "content": "from A"}', 1)
    turn(a["session_id"], '/tool write {"file_path": "only-a.txt", "content": "A made this"}', 2)
    # B's sync runs inside its turn (its conflict is reported in the run's events),
    # so once the turn has ended the scope holds whatever won.
    run = turn(b["session_id"], '/tool write {"file_path": "shared.txt", "content": "from B"}', 1)
    content = alice.get(f"/v1/scopes/{scope}/files/shared.txt").text
    check("the session that wrote second does not overwrite the first", content == "from A", content)
    errors = [p for p in payloads(run, "error") if p.get("error_code") == "WORKSPACE_CONFLICT"]
    check("the losing session is told which file conflicted", errors and "shared.txt" in errors[0].get("detail", ""),
          payloads(run, "error"))
    later = invoke(b["session_id"], "read", file_path="shared.txt")
    check("the losing session's workspace is refreshed from the scope", later.get("payload") == "from A", later)
    r = alice.get(f"/v1/scopes/{scope}/files/only-a.txt")
    check("a file created by another session since is not deleted", r.status_code == 200 and r.text == "A made this",
          (r.status_code, r.text[:80]))

# --- E. sandbox loss: worker killed --------------------------------------------------
print("\n## E. When the worker holding the sandbox dies")
_, plain = session(full)
invoke(plain["session_id"], "write", file_path="work.txt", content="unsaved work")
_, scoped = session(full, scope=scope)
turn(scoped["session_id"], '/tool write {"file_path": "saved.txt", "content": "saved work"}', 1)
ctl("kill", "worker")
ctl("start", "worker")
r = invoke(plain["session_id"], "read", file_path="work.txt")
check("no scope: files survive a worker crash", r.get("payload") == "unsaved work", r)
r = invoke(scoped["session_id"], "read", file_path="saved.txt")
check("scope: the adopting worker rehydrates the workspace", r.get("payload") == "saved work", r)

(Path(os.environ["E2E_RUN_DIR"]) / "stage4_state.json").write_text(json.dumps({"scope": scope}))
print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
for name, ok, detail in results:
    if not ok:
        print("  FAIL", name, "—", detail)

sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
