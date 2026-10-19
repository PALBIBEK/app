"""Stage 2 — harness and catalog management, as a user does it, through the API.

Every check prints PASS/FAIL with the evidence; nothing here reaches into the
supervisor's internals. Run: $PY stage2.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from client import User

HARNESS = Path(os.environ["REPO"]) / "harness"
REAL_IMAGE = "sha256:" + hashlib.sha256(b"githi-runtime-image").hexdigest()
COMMON = {"semver": "1.0.0", "protocol_version": 1, "status": "offered"}
results: list[tuple[str, bool, str]] = []
STATE: dict[str, object] = {}


def check(name: str, ok: bool, detail: object = "") -> bool:
    results.append((name, ok, str(detail)[:240]))
    print(("PASS " if ok else "FAIL ") + name + (f"  — {str(detail)[:240]}" if detail != "" else ""))
    return ok


def local(kind_dir: str, name: str) -> dict:
    body = json.loads((HARNESS / kind_dir / f"{name}.json").read_text())
    body.pop("digest", None)
    return body


u = User("t1", "alice")
other = User("t2", "bob")


def publish(kind: str, body: dict) -> tuple[int, dict]:
    r = u.publish(kind, body)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text}


def ref(resp: dict) -> dict:
    return {"id": resp["artifact_id"], "digest": resp["digest"]}


def create(comp: str, digest: str, harness: str = "githi-v1", user: User = u) -> tuple[int, dict]:
    r = user.create(comp, digest, harness)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"raw": r.text}


# --- 1. shipped harnesses, as they come ------------------------------------------
print("\n## 1. Use a shipped harness as-is")
shipped = {a["artifact_id"]: a for a in u.get("/v1/catalog/compositions").json()}
for name in ("githi-v1", "githi-tools-v1", "coding-v1", "coding-durable-v1"):
    harness = json.loads((HARNESS / "compositions" / f"{name}.json").read_text())["harness_version"]
    code, body = create(name, shipped[name]["digest"], harness)
    check(f"shipped composition {name} ({harness}) starts a session", code == 201, f"{code} {body}")

# --- 2. a sandbox the user can run here ---------------------------------------------
print("\n## 2. Publish a sandbox profile and a runnable copy of githi-v1")
code, sb = publish("sandbox_profile", COMMON | {"name": "local-sandbox", "provider": "local_process",
                                                "image_digest": REAL_IMAGE, "file_effects": "workspace_write",
                                                "approval_default": "ask"})
check("publish local_process sandbox profile", code == 200, sb)
base = local("compositions", "githi-v1") | {"sandbox_profile": ref(sb)}
code, mine = publish("composition", base | {"name": "my-githi"})
check("publish my-githi (shipped githi-v1 + local sandbox)", code == 200, mine)
code, s = create("my-githi", mine["digest"])
check("my-githi starts a session", code == 201, s)
STATE["my-githi"] = mine

# --- 3. modify a registered harness ---------------------------------------------------
print("\n## 3. Modify it: new prompt, tool set, memory → new versions; old pins keep working")
r = u.get(f"/v1/catalog/composition/my-githi/{mine['digest']}")
check("read a published composition's content via the API", "prompt_pack" in r.json(), list(r.json()))

prompt = local("prompt_packs", "coding-prompt")
prompt2 = copy.deepcopy(prompt) | {"semver": "1.1.0"}
prompt2["sections"][1]["text"] = "Prefer small diffs. Always explain the change in one line."
code, p2 = publish("prompt_pack", prompt2)
check("publish coding-prompt 1.1.0 (changed rule)", code == 200 and p2["digest"] != json.loads(
    (HARNESS / "prompt_packs" / "coding-prompt.json").read_text())["digest"], p2)

code, again = publish("prompt_pack", prompt2)
check("re-publishing identical content is idempotent (same digest)", code == 200 and again["digest"] == p2["digest"], again)

tools = local("tool_sets", "githi-tools")
readonly = {"name": "readonly-tools", **COMMON,
            "definitions": {k: v for k, v in tools["definitions"].items() if k in ("read", "ls", "grep")}}
code, ro = publish("tool_set", readonly)
check("publish a read-only tool set (subset of githi-tools)", code == 200, ro)

mem_off = local("memory_profiles", "default") | {"name": "no-compaction"}
mem_off["compaction"] = dict(mem_off["compaction"], enabled=False)
code, moff = publish("memory_profile", mem_off)
check("publish a memory profile with compaction off", code == 200, moff)

code, v2 = publish("composition", base | {"name": "my-githi", "semver": "2.0.0", "prompt_pack": ref(p2),
                                          "tool_set": ref(ro), "memory_profile": ref(moff)})
check("publish my-githi 2.0.0 pinning the new prompt/tools/memory", code == 200 and v2["digest"] != mine["digest"], v2)
versions = [a for a in u.get("/v1/catalog/compositions").json() if a["artifact_id"] == "my-githi"]
check("both versions of my-githi are listed", len(versions) >= 2, [(a["semver"], a["digest"][:15]) for a in versions])
check("v1 still starts sessions", create("my-githi", mine["digest"])[0] == 201)
code, s2 = create("my-githi", v2["digest"])
check("v2 starts sessions", code == 201, s2)
STATE["my-githi-v2"] = v2

# --- 4. register new compositions across every seam ----------------------------------
print("\n## 4. New compositions: every seam option")
coding_tools = local("tool_sets", "coding-tools")
all_defs = tools["definitions"] | coding_tools["definitions"]
code, full = publish("tool_set", {"name": "power-tools", **COMMON, "definitions": all_defs})
check("tool set with bash + git_commit (coding-tools ∪ githi-tools)", code == 200, full)

edit_def = {"name": "edit", "description": "Replace exact text in a file", "tool_class": "idempotent",
            "parameters": {"type": "object", "properties": {"file_path": {"type": "string"},
                                                              "old_text": {"type": "string"},
                                                              "new_text": {"type": "string"}},
                           "required": ["file_path", "old_text", "new_text"]}}
code, with_edit = publish("tool_set", {"name": "edit-tools", **COMMON, "definitions": {"edit": edit_def, "read": tools["definitions"]["read"]}})
check("tool set with the registry-only `edit` tool", code == 200, with_edit)

bad = {"name": "weather", "description": "w", "parameters": {"type": "object"}, "tool_class": "pure"}
code, refused = publish("tool_set", {"name": "bad-tools", **COMMON, "definitions": {"weather": bad}})
check("tool set naming an unimplemented tool is refused (422)", code == 422, refused)

code, empty = publish("tool_set", {"name": "no-tools", **COMMON, "definitions": {}})
check("tool set with no tools", code == 200, empty)

prompt_custom = {"name": "analyst-prompt", **COMMON, "persona": "analyst", "vars": [],
                 "sections": [{"id": "s1", "order": 1, "text": "You are a data analyst. Answer in bullet points.", "complete": True}]}
code, pc = publish("prompt_pack", prompt_custom)
check("custom prompt pack (new persona, own sections)", code == 200, pc)

memories = {}
for mname, recall in (("recall-markdown", {"enabled": True, "backend": "githi_markdown"}),
                      ("recall-durable", {"enabled": True, "backend": "durable"}),
                      ("recall-mcp-no-url", {"enabled": True, "backend": "mcp"})):
    body = local("memory_profiles", "default") | {"name": mname, "recall": recall}
    code, m = publish("memory_profile", body)
    check(f"memory profile {mname}", code == 200, m)
    memories[mname] = m

sandboxes = {}
for sname, fields in (("sb-readonly", {"provider": "local_process", "file_effects": "read_only"}),
                      ("sb-full", {"provider": "local_process", "file_effects": "danger_full_access"}),
                      ("sb-docker", {"provider": "local_docker"}),
                      ("sb-e2b", {"provider": "e2b"}),
                      ("sb-none", {"provider": "none"})):
    body = COMMON | {"name": sname, "image_digest": REAL_IMAGE, "approval_default": "ask",
                     "file_effects": "workspace_write"} | fields
    code, sbr = publish("sandbox_profile", body)
    check(f"sandbox profile {sname}", code == 200, sbr)
    sandboxes[sname] = sbr

combos = {
    "c-readonly-tools": {"tool_set": ref(ro)},
    "c-power-tools": {"tool_set": ref(full)},
    "c-edit-tools": {"tool_set": ref(with_edit)} if "digest" in with_edit else None,
    "c-no-tools": {"tool_set": ref(empty)} if "digest" in empty else None,
    "c-analyst-prompt": {"prompt_pack": ref(pc)},
    "c-no-memory": {"memory_profile": None},
    "c-recall-markdown": {"memory_profile": ref(memories["recall-markdown"])},
    "c-recall-durable": {"memory_profile": ref(memories["recall-durable"])},
    "c-recall-mcp-no-url": {"memory_profile": ref(memories["recall-mcp-no-url"])},
    "c-sb-readonly": {"sandbox_profile": ref(sandboxes["sb-readonly"])},
    "c-sb-full": {"sandbox_profile": ref(sandboxes["sb-full"])},
    "c-sb-docker": {"sandbox_profile": ref(sandboxes["sb-docker"])},
    "c-sb-e2b": {"sandbox_profile": ref(sandboxes["sb-e2b"])},
    "c-sb-none": {"sandbox_profile": ref(sandboxes["sb-none"])},
    "c-route-chat": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "standin-chat"}},
    "c-route-responses": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "standin-responses"}},
    "c-route-claude": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "standin-claude"}},
    "c-route-discovered": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "gateway/standin-claude"}},
    "c-route-unknown": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "no-such-model"}},
    "c-other-gateway-url": {"plug_bindings": {"gateway_base_url": "http://127.0.0.1:8090", "model_route": "standin-chat"}},
    "c-acp-subagents": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "standin-chat", "acp_base_url": "http://acp:9000"}},
    "c-tiny-window": {"plug_bindings": {"gateway_base_url": "http://model-gateway:8090", "model_route": "tiny-window"}},
}
STATE["combos"] = {}
for cname, change in combos.items():
    if change is None:
        check(f"composition {cname}", False, "prerequisite artifact was not published")
        continue
    body = {k: v for k, v in (base | {"name": cname} | change).items() if v is not None}
    code, c = publish("composition", body)
    if not check(f"publish composition {cname}", code == 200, c):
        continue
    code, s = create(cname, c["digest"])
    expect_refused = cname in ("c-sb-none", "c-sb-e2b", "c-sb-docker")
    ok = (code >= 400) if expect_refused else (code == 201)
    check(f"  session from {cname} {'is refused clearly' if expect_refused else 'starts'}", ok, f"{code} {s}")
    STATE["combos"][cname] = {"digest": c["digest"], "session": s.get("session_id")}

# other harness runtimes over the same seams
for harness, name in (("coding-v1", "my-coding"), ("kg-v1", "my-kg")):
    body = base | {"name": name, "harness_version": harness}
    code, c = publish("composition", body)
    if check(f"publish {name} on harness {harness}", code == 200, c):
        code, s = create(name, c["digest"], harness)
        check(f"  session from {name} ({harness}) starts", code == 201, f"{code} {s}")

# --- 5. pinning rules -----------------------------------------------------------------
print("\n## 5. Pinning, deprecation, invalid input")
check("floating ref 'latest' is refused", create("my-githi", "latest")[0] in (400, 404, 409, 422))
check("another composition's digest is refused", create("my-githi", shipped["githi-v1"]["digest"])[0] == 404)
code, body = create("my-githi", mine["digest"], harness="coding-v1")
check("harness_version mismatch is refused", code in (400, 409, 422), f"{code} {body}")
forged = prompt2 | {"digest": p2["digest"], "persona": "evil"}
code, body = publish("prompt_pack", forged)
check("forged digest (content changed, digest kept) is refused (422)", code == 422, f"{code} {body}")
code, body = publish("composition", {"name": "broken", **COMMON})
check("incomplete composition body is refused with 4xx", 400 <= code < 500, f"{code} {body}")

code, dep = publish("composition", base | {"name": "to-deprecate"})
_, live = create("to-deprecate", dep["digest"])
r = u.post(f"/v1/catalog/composition/to-deprecate/{dep['digest']}/deprecate")
check("deprecate a composition", r.status_code == 204, r.status_code)
check("deprecated composition starts no new sessions", create("to-deprecate", dep["digest"])[0] in (404, 409, 410, 422))
r = u.say(live["session_id"], "still there?")
check("a session already on the deprecated composition keeps working", r.status_code == 202, r.status_code)

# tenant boundaries on the shared catalog
r = other.post(f"/v1/catalog/composition/my-githi/{mine['digest']}/deprecate")
check("another tenant cannot deprecate my composition", r.status_code in (403, 404), r.status_code)
code, body = create("my-githi", mine["digest"], user=other)
print(f"     (info) another tenant creating a session from my composition → {code}")
STATE["cross-tenant-create"] = code

# --- 6. restart: everything survives ---------------------------------------------------
print("\n## 6. Restart API and worker; the user's catalog survives")
subprocess.run([os.environ["E2E_CTL"], "restart", "api"], check=True)
subprocess.run([os.environ["E2E_CTL"], "restart", "worker"], check=True)
for _ in range(60):
    try:
        if u.get("/readyz").status_code == 200:
            break
    except httpx.HTTPError:  # not listening yet
        pass
    time.sleep(1)
names = {a["artifact_id"] for a in u.get("/v1/catalog/compositions").json()}
check("published compositions are listed after restart", {"my-githi", "c-power-tools"} <= names, sorted(names)[:8])
check("v2 still starts sessions after restart", create("my-githi", v2["digest"])[0] == 201)
check("deprecation survived the restart", create("to-deprecate", dep["digest"])[0] != 201)

Path(os.environ["E2E_RUN_DIR"]) / "stage2_state.json".write_text(json.dumps(STATE, indent=1, default=str))
print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} checks passed")
for name, ok, detail in results:
    if not ok:
        print("  FAIL", name, "—", detail)

sys.exit(1 if any(not ok for _, ok, _ in results) else 0)
