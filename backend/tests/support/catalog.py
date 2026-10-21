"""A fake supervisor catalog that serves the shipped githi-v1 composition as the live one does.

Built from the shipped files, so a test pins the digest the real catalog offers and
breaks when the shipped artifacts change in a way the backend would have to follow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

HARNESS_DIR = Path(__file__).resolve().parents[3] / "prime-agent-study-main" / "harness"
GITHI = json.loads((HARNESS_DIR / "compositions" / "githi-v1.json").read_text())
GITHI_TOOLS = json.loads((HARNESS_DIR / "tool_sets" / "githi-tools.json").read_text())

DIGEST = GITHI["digest"]
PIN: dict[str, str] = {
    "composition_id": "githi-v1",
    "composition_digest": DIGEST,
    "harness_version": "githi-v1",
    "runtime": "githi",
}
"""What a harness bound to the default composition stores."""


def _artifact(kind: str, artifact_id: str, digest: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "artifact_id": artifact_id,
        "digest": digest,
        "status": "offered",
        "semver": body.get("semver", "1.0.0"),
        "body": body,
    }


def catalog_response(request: httpx.Request) -> httpx.Response | None:
    """The supervisor's answer to a catalog or harness-version read, or None for anything else."""
    path = request.url.path
    if request.method != "GET":
        return None
    if path.endswith("/v1/catalog/compositions"):
        listed = {k: v for k, v in _artifact("composition", "githi-v1", DIGEST, GITHI).items() if k != "body"}
        return httpx.Response(200, json=[listed])
    if "/v1/catalog/composition/githi-v1/" in path:
        if not request.url.raw_path.decode().endswith(DIGEST.replace(":", "%3A")):
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json=_artifact("composition", "githi-v1", DIGEST, GITHI))
    if "/v1/catalog/tool_set/githi-tools/" in path:
        return httpx.Response(200, json=_artifact("tool_set", "githi-tools", "sha256:set", GITHI_TOOLS))
    if path.endswith("/v1/harness_versions/githi-v1"):
        return httpx.Response(200, json={"harness_version": "githi-v1", "runtime": "githi", "status": "available"})
    if path.endswith("/readyz"):
        return httpx.Response(200, json={"status": "ready"})
    return None
