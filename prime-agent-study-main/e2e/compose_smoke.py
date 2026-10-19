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


if __name__ == "__main__":
    main()
