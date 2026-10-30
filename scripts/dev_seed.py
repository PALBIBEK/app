"""Seed the local product for ./dev.sh: a demo user and one harness ready to chat.

Idempotent: logs in when the user exists and leaves a published demo harness alone.
The harness is published through the builder's own routes (manifest, then publish).
"""

from __future__ import annotations

import os
import sys

import httpx

API = "http://127.0.0.1:8000/api/v1"
ORIGIN = "http://localhost:3000"
HARNESS = "Demo coding agent"


def main() -> int:
    email, password = os.environ["DEMO_EMAIL"], os.environ["DEMO_PASSWORD"]
    with httpx.Client(base_url=API, headers={"Origin": ORIGIN}, timeout=30) as http:
        # Sign in first: registering on every start would spend the address's registrations
        # (10 an hour), and a restart loop would lock the seed out.
        r = http.post("/auth/login", json={"email": email, "password": password})
        if r.status_code == 401:
            r = http.post("/auth/register", json={"email": email, "password": password, "name": "Demo"})
        r.raise_for_status()
        http.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

        listed = http.get("/harnesses", params={"page_size": 100})
        listed.raise_for_status()
        existing = next((h for h in listed.json()["data"] if h["name"] == HARNESS), None)
        if existing is None:
            created = http.post("/harnesses", json={
                "name": HARNESS,
                "description": "A coding agent with shell, files and edit; bash asks first.",
                "model": "gateway/echo",
                "mode": "sticky",
            })
            created.raise_for_status()
            existing = created.json()
        if existing.get("bind", {}).get("bind_status") != "bound":
            # Published the way the builder does it: the default manifest, run as a host process
            # (no Docker needed), where bash only runs with full access. It still asks first.
            hid = existing["id"]
            manifest = http.get(f"/harnesses/{hid}/manifest").raise_for_status().json()["manifest"]
            manifest["sandbox"].update(provider="local_process", file_effects="danger_full_access")
            http.put(f"/harnesses/{hid}/manifest", json=manifest).raise_for_status()
            published = http.post(f"/harnesses/{hid}/publish", timeout=120)
            if published.status_code >= 400:
                print(f"seed: harness created but not published: {published.status_code} {published.text}",
                      file=sys.stderr)
                return 1
    print(f"seed: user {email}, harness '{HARNESS}' published")
    return 0


if __name__ == "__main__":
    sys.exit(main())
