"""Seed the local product for ./dev.sh: a demo user and one harness ready to chat.

Idempotent: logs in when the user exists and leaves an existing demo harness alone.
The harness is bound to the shipped ``githi-v1`` composition at the digest the
supervisor's catalog offers (read from the shipped file, then checked live by the bind).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:8000/api/v1"
ORIGIN = "http://localhost:3000"
HARNESS = "Demo coding agent"
ROOT = Path(__file__).resolve().parent.parent
COMPOSITION = ROOT / "prime-agent-study-main/harness/compositions/githi-v1.json"


def main() -> int:
    email, password = os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"]
    with httpx.Client(base_url=API, headers={"Origin": ORIGIN}, timeout=30) as http:
        r = http.post("/auth/register", json={"email": email, "password": password, "name": "Demo"})
        if r.status_code not in (201, 400, 409):
            r.raise_for_status()
        if r.status_code != 201:
            r = http.post("/auth/login", json={"email": email, "password": password})
            r.raise_for_status()
        http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

        listed = http.get("/harnesses", params={"page_size": 100})
        listed.raise_for_status()
        existing = next((h for h in listed.json()["data"] if h["name"] == HARNESS), None)
        if existing is None:
            created = http.post("/harnesses", json={
                "name": HARNESS,
                "description": "Shipped githi coding agent: shell, files, edit.",
                "model": "claude-sonnet-4",  # a label only until Phase 2: the composition picks the route
                "mode": "sticky",
            })
            created.raise_for_status()
            existing = created.json()
        if existing.get("bind_status") != "bound":
            digest = json.loads(COMPOSITION.read_text())["digest"]
            bound = http.post(f"/harnesses/{existing['id']}/bind", json={
                "composition_id": "githi-v1", "composition_digest": digest, "runtime": "githi",
            })
            if bound.status_code >= 400:
                print(f"seed: harness created but not bound: {bound.status_code} {bound.text}", file=sys.stderr)
                return 1
    print(f"seed: user {email}, harness '{HARNESS}' bound to githi-v1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
