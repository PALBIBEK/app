"""Smoke test for the docker-compose stack: the supervisor is ready and a session runs a turn.

Run against `docker compose up` from the project root:
    python e2e/compose_smoke.py
Exits non-zero (with the last events seen) if any step fails.
"""

from __future__ import annotations

import os
import sys
import time
from typing import NoReturn

import httpx

from client import SUP, User, runs_ended, types


def wait_ready(timeout_s: float = 300) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            r = httpx.get(f"{SUP}/readyz", timeout=5)
            if r.status_code == 200:
                return
            last = f"{r.status_code} {r.text[:200]}"
        except httpx.HTTPError as exc:
            last = repr(exc)
        time.sleep(3)
    fail(f"supervisor never became ready: {last}")


def fail(message: str) -> NoReturn:
    """Exit non-zero; in CI the reason also becomes an annotation."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::error title=compose smoke::{message}")
    sys.exit(message)


def main() -> None:
    wait_ready()
    memory = httpx.get("http://127.0.0.1:8092/v1/readyz", timeout=10)
    if memory.status_code != 200:
        fail(f"the memory service is not ready: {memory.status_code} {memory.text[:200]}")
    user = User("smoke", "ci")
    r = user.post("/v1/sessions", {"harness_version": "kg-v1"})
    if r.status_code != 201:
        fail(f"session create failed: {r.status_code} {r.text[:300]}")
    sid = r.json()["session_id"]
    if user.say(sid, "hello from CI").status_code != 202:
        fail("message refused")
    try:
        evs = user.wait(sid, runs_ended(1), timeout=120)
    except TimeoutError as exc:
        fail(f"the turn never finished: {exc}")
    if "error" in types(evs):
        fail(f"the turn failed: {[e for e in evs if e.get('type') == 'error']}")
    print(f"compose smoke: session {sid} ran a turn: {types(evs)}")
    githi_runs_a_tool_in_its_pinned_image(user)


def githi_runs_a_tool_in_its_pinned_image(user: User) -> None:
    """The shipped githi agent on Docker (githi-tools-v1), runs a shell in its pinned image.

    (githi-v1 is the same agent without a sandbox: safe file tools only, no shell.)"""
    shipped = {a["artifact_id"]: a for a in user.get("/v1/catalog/compositions").json()}
    if "githi-tools-v1" not in shipped:
        fail(f"githi-tools-v1 is not in the shipped catalog: {sorted(shipped)}")
    r = user.create("githi-tools-v1", shipped["githi-tools-v1"]["digest"], harness="githi-v1")
    if r.status_code != 201:
        fail(f"githi-tools-v1 session create failed: {r.status_code} {r.text[:300]}")
    sid = r.json()["session_id"]
    user.say(sid, '/tool bash {"command": "git --version && pwd"}')
    try:
        evs = user.wait(sid, runs_ended(1), timeout=240)  # the first run pulls the image
    except TimeoutError as exc:
        fail(f"the githi turn never finished: {exc}")
    ends = [e["data"] for e in evs if e.get("type") == "tool_end"]
    result = (ends[0].get("result") or {}) if ends else {}
    if not ends or result.get("ok") is not True or "git version" not in str(result):
        fail(f"githi's tool did not run in its sandbox: {types(evs)} {str(result)[:300]}")
    print(f"compose smoke: githi-tools-v1 ran bash in its sandbox image: {str(result.get('payload'))[:120]}")


if __name__ == "__main__":
    main()
