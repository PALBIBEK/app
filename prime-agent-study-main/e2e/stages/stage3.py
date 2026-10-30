"""Stage 3 — does each configuration reach the model? Real turns through the gateway.

For every composition: send a turn, then check the journal (what the agent did)
and the relay log (exactly what the model received: wire, system prompt, tools,
replayed reasoning). Run: $PY stage3.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from client import User, payloads, relay_mark, relay_since, runs_ended, types

HARNESS = Path(os.environ["REPO"]) / "harness"
REAL_IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
GITHI_TOOLS = {"echo", "grep", "ls", "read", "remember", "write"}
PLATFORM = {"memory", "history", "subagent", "subagent_fork", "subagent_result"}
results: list[tuple[str, bool, str]] = []
u = User("t1", "alice")


def check(name: str, ok: bool, detail: object = "") -> bool:
    ok = bool(ok)
    results.append((name, ok, str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:300]}" if detail != "" else ""))
    return ok


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


def publish(kind: str, body: dict) -> dict:
    r = u.publish(kind, body)
    assert r.status_code == 200, (kind, body.get("name"), r.status_code, r.text)
    return r.json()


def ref(resp: dict) -> dict:
    return {"id": resp["artifact_id"], "digest": resp["digest"]}


SANDBOX = ref(publish("sandbox_profile", COMMON | {
    "name": "local-sandbox", "provider": "local_process", "image_digest": REAL_IMAGE,
    "file_effects": "workspace_write", "approval_default": "ask"}))
BASE = local("compositions", "githi-v1") | {"sandbox_profile": SANDBOX}


def composition(name: str, route: str = "standin-chat", url: str = "http://model-gateway:8090", **change: object) -> str:
    bindings = {"gateway_base_url": url, "model_route": route}
    if "acp" in change:
        bindings["acp_base_url"] = change.pop("acp")
    body = BASE | {"name": name, "plug_bindings": bindings} | change
    return publish("composition", body)["digest"]


def session(name: str, digest: str) -> str:
    r = u.create(name, digest)
    assert r.status_code == 201, (name, r.status_code, r.text)
    return r.json()["session_id"]


def turn(sid: str, text: str, n: int, timeout: float = 90) -> tuple[list[dict], list[dict], list[dict]]:
    """Send one message; return (this run's events, all events, upstream requests during it)."""
    mark = relay_mark()
    r = u.say(sid, text)
    assert r.status_code == 202, (r.status_code, r.text)
    evs = u.wait(sid, runs_ended(n), timeout=timeout)
    starts = [i for i, t in enumerate(types(evs)) if t == "run_start"]
    return evs[starts[n - 1]:], evs, relay_since(mark)


def tool_names(body: dict) -> set[str]:
    out = set()
    for t in body.get("tools") or []:
        out.add(t.get("name") or (t.get("function") or {}).get("name"))
    return out


def system_text(call: dict) -> str:
    body = call["body"]
    if call["path"] == "/v1/messages":
        system = body.get("system")
        return system if isinstance(system, str) else " ".join(b.get("text", "") for b in system or [])
    if call["path"] == "/v1/responses":
        parts = [body.get("instructions") or ""]
        for item in body.get("input") or []:
            if item.get("role") in ("system", "developer"):
                c = item.get("content")
                parts.append(c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or []))
        return " ".join(parts)
    return " ".join(
        m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
        for m in body.get("messages") or [] if m.get("role") in ("system", "developer")
    )


def final_text(run: list[dict]) -> str:
    finals = [p for p in payloads(run, "MessageEnd") if p["role"] == "assistant"]
    return " ".join(c.get("text", "") for c in finals[-1]["content"] if c.get("type") == "text") if finals else ""


# --- A. each wire protocol ---------------------------------------------------------
print("\n## A. Wire protocols: the route decides the protocol; prompt, tools and replay reach the model")
for route, path in (("standin-chat", "/v1/chat/completions"), ("standin-responses", "/v1/responses"),
                    ("standin-claude", "/v1/messages"), ("gateway/standin-claude", "/v1/messages"),
                    ("gateway/standin-chat", "/v1/chat/completions")):
    sid = session(f"w-{route.replace('/', '-')}", composition(f"w-{route.replace('/', '-')}", route))
    run, _, calls = turn(sid, '/tool ls {"path": "."}', 1)
    ends = payloads(run, "ToolEnd")
    check(f"[{route}] turn completes with a tool call", bool(ends) and ends[0]["result"].get("ok") is True,
          types(run))
    check(f"[{route}] model reached over {path}", len(calls) >= 2 and all(c["path"] == path for c in calls),
          [c["path"] for c in calls])
    if calls:
        check(f"[{route}] pinned system prompt reached the model", "You are an expert coding assistant." in system_text(calls[0]),
              system_text(calls[0])[:150])
        offered = tool_names(calls[0]["body"])
        check(f"[{route}] pinned tools + platform tools offered", GITHI_TOOLS | {"memory", "history"} <= offered, sorted(offered))
    if path == "/v1/messages" and len(calls) >= 2:
        replay = [b for m in calls[1]["body"].get("messages", []) if m.get("role") == "assistant"
                  for b in (m.get("content") if isinstance(m.get("content"), list) else []) if b.get("type") == "thinking"]
        check(f"[{route}] signed thinking replayed on the next step",
              bool(replay) and all(b.get("signature") for b in replay), replay[:1])
    if path == "/v1/responses" and len(calls) >= 2:
        reasoning = [i for i in calls[1]["body"].get("input", []) if i.get("type") == "reasoning"]
        check(f"[{route}] encrypted reasoning replayed (stateless, store=false)",
              bool(reasoning) and all(i.get("encrypted_content") for i in reasoning)
              and calls[1]["body"].get("store") is False, (reasoning[:1], calls[1]["body"].get("store")))
    check(f"[{route}] final answer reflects the tool result", final_text(run).startswith("echo:"), final_text(run)[:120])

# --- B. prompts -------------------------------------------------------------------
print("\n## B. Prompt changes reach the model")
prompt2 = local("prompt_packs", "coding-prompt") | {"name": "my-coding-prompt", "semver": "1.1.0"}
prompt2["sections"] = [dict(s) for s in prompt2["sections"]]
prompt2["sections"][-1]["text"] += " Always explain the change in one line."
p2 = ref(publish("prompt_pack", prompt2))
analyst = ref(publish("prompt_pack", {"name": "analyst-prompt", **COMMON, "persona": "analyst", "vars": [],
                                      "sections": [{"id": "s1", "order": 1, "complete": True,
                                                    "text": "You are a data analyst. Answer in bullet points."}]}))
for name, pref, expect, absent in (("p-coding-1-1", p2, "Always explain the change", None),
                                   ("p-analyst", analyst, "You are a data analyst", "You are an expert coding assistant")):
    sid = session(name, composition(name, prompt_pack=pref))
    _, _, calls = turn(sid, "hello", 1)
    text = system_text(calls[0]) if calls else ""
    check(f"[{name}] system prompt is the pinned version", expect in text and (absent is None or absent not in text), text[:160])

# --- C. tools ---------------------------------------------------------------------
print("\n## C. Tool sets: exactly the pinned tools, running in the sandbox")
tools = local("tool_sets", "githi-tools")["definitions"]
coding = local("tool_sets", "coding-tools")["definitions"]
ro = ref(publish("tool_set", {"name": "readonly-tools", **COMMON, "definitions": {k: tools[k] for k in ("read", "ls", "grep")}}))
power = ref(publish("tool_set", {"name": "power-tools", **COMMON, "definitions": tools | coding}))
none_ = ref(publish("tool_set", {"name": "no-tools", **COMMON, "definitions": {}}))

sid = session("t-readonly", composition("t-readonly", tool_set=ro))
run, _, calls = turn(sid, "hello", 1)
offered = tool_names(calls[0]["body"]) if calls else set()
check("[readonly] only read/ls/grep (+ platform) offered", offered & (GITHI_TOOLS | {"bash"}) == {"read", "ls", "grep"}, sorted(offered))
r = u.post(f"/v1/sessions/{sid}/tools/invoke", {"name": "write", "arguments": {"file_path": "x.txt", "content": "hi"}})
check("[readonly] invoking an unpinned tool is refused (TOOL_NOT_FOUND)", r.status_code == 200 and r.json().get("code") == "TOOL_NOT_FOUND", (r.status_code, r.text[:200]))

FULL = ref(publish("sandbox_profile", COMMON | {
    "name": "sb-full", "provider": "local_process", "image_digest": REAL_IMAGE,
    "file_effects": "danger_full_access", "approval_default": "ask"}))
sid = session("t-power-default", composition("t-power-default", tool_set=power))
run, _, _ = turn(sid, '/tool bash {"command": "echo hi"}', 1)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("[power, workspace_write profile] bash is refused by policy (FILE_EFFECTS_DENIED)", (end.get("result") or {}).get("code") == "FILE_EFFECTS_DENIED", end.get("result"))
sid = session("t-power", composition("t-power", tool_set=power, sandbox_profile=FULL))
run, _, _ = turn(sid, '/tool bash {"command": "echo hello > made.txt && cat made.txt && pwd"}', 1)
end = (payloads(run, "ToolEnd") or [{}])[0]
out = (end.get("result") or {}).get("payload") or {}
check("[power] bash runs in the sandbox and sees its own files", out.get("exit_code") == 0 and "hello" in out.get("stdout", ""), out)
check("[power] bash does not run in the supervisor's session directory",
      os.environ["YAL_SESSION_WORKSPACES"] not in out.get("stdout", ""), out.get("stdout"))
run, _, _ = turn(sid, '/tool read {"file_path": "made.txt"}', 2)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("[power] read sees the file bash wrote (same sandbox across turns)", (end.get("result") or {}).get("payload") == "hello\n", end.get("result"))
run, _, _ = turn(sid, '/tool bash {"command": "env | grep -iE \'token|secret|database_url|api_key\' || echo CLEAN"}', 3)
out = ((payloads(run, "ToolEnd") or [{}])[0].get("result") or {}).get("payload") or {}
check("[power] no supervisor secrets in the agent's environment", out.get("stdout", "").strip() == "CLEAN", out.get("stdout"))
run, _, _ = turn(sid, '/tool git_commit {"message": "first"}', 4)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("[power] git_commit runs (reports when the workspace is not a repo)", (end.get("result") or {}).get("ok") is True, end.get("result"))

sid = session("t-none", composition("t-none", tool_set=none_))
_, _, calls = turn(sid, "hello", 1)
offered = tool_names(calls[0]["body"]) if calls else set()
check("[no-tools] no catalog tools offered (platform tools only)", not (offered & (GITHI_TOOLS | {"bash"})), sorted(offered))

# --- D. model routes ---------------------------------------------------------------
print("\n## D. Model routes")
r = u.publish("composition", BASE | {"name": "r-unknown", "plug_bindings": {
    "gateway_base_url": "http://model-gateway:8090", "model_route": "no-such-model"}})
check("[unknown route] refused at publish, before any session can start on it (F17)",
      r.status_code == 422 and "not served" in r.text, (r.status_code, r.text[:200]))
sid = session("r-other-url", composition("r-other-url", url="http://127.0.0.1:8090"))
run, _, calls = turn(sid, '/tool ls {"path": "."}', 1)
check("[different gateway URL] still answers (chat-completions client)", "run_end" in types(run) and bool(calls), (types(run), [c["path"] for c in calls]))

# --- E. subagents -----------------------------------------------------------------
print("\n## E. Subagents: child sessions, and background jobs on the pinned transport")
sid = session("s-nested", composition("s-nested"))
mark = relay_mark()
run, _, calls = turn(sid, '/tool subagent {"prompt": "summarise the repo", "description": "summary"}', 1)
start, end = (payloads(run, "SubagentStart") or [{}])[0], (payloads(run, "SubagentEnd") or [{}])[0]
check("[child] subagent runs as a child session", start.get("provider") == "session" and start.get("child_session_id"), start)
check("[child] the child's model call went through the gateway, as a subagent",
      any("summarise the repo" in json.dumps(c["body"]) and "You are a subagent" in json.dumps(c["body"]) for c in calls),
      len(calls))
check("[child] the child's answer comes back to the parent", bool(end.get("output")), end)

run, allev, _ = turn(sid, '/tool subagent {"prompt": "long background task", "background": true}', 2)
started = ((payloads(run, "ToolEnd") or [{}])[0].get("result") or {}).get("payload") or {}
check("[background] starts and returns a job_id", started.get("status") == "started" and started.get("job_id"), started)
u.wait(sid, lambda evs: any(e["type"] == "job_end" for e in evs), timeout=60)
run, _, calls = turn(sid, "anything new?", 3)
announced = [c for c in calls if "Background subagents you started have finished" in json.dumps(c["body"])]
check("[background] its result is announced to the model on the next turn", bool(announced), len(calls))

# --- F. long-term memory tool (memory service) ---------------------------------------
print("\n## F. remember → memory service")
sid = session("m-remember", composition("m-remember"))
run, _, _ = turn(sid, '/tool remember {"content": "The user prefers tea over coffee"}', 1)
end = (payloads(run, "ToolEnd") or [{}])[0]
check("[remember] stores a fact in the memory service", (end.get("result") or {}).get("ok") is True, end.get("result"))

print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
for name, ok, detail in results:
    if not ok:
        print("  FAIL", name, "—", detail)

sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
