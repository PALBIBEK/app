"""Smoke test for the docker-compose stack: the supervisor is ready and a session runs a turn.

Run against `docker compose up` from the project root:
    python e2e/compose_smoke.py
Exits non-zero (with the last events seen) if any step fails.
"""

from __future__ import annotations

import sys
import time

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
    sys.exit(f"supervisor never became ready: {last}")


def main() -> None:
    wait_ready()
    user = User("smoke", "ci")
    r = user.post("/v1/sessions", {"harness_version": "kg-v1"})
    if r.status_code != 201:
        sys.exit(f"session create failed: {r.status_code} {r.text[:300]}")
    sid = r.json()["session_id"]
    if user.say(sid, "hello from CI").status_code != 202:
        sys.exit("message refused")
    evs = user.wait(sid, runs_ended(1), timeout=120)
    if "error" in types(evs):
        sys.exit(f"the turn failed: {types(evs)}")
    print(f"compose smoke: session {sid} ran a turn: {types(evs)}")


if __name__ == "__main__":
    main()
