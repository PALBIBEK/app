"""ACP-shaped SubagentProvider — HTTP remote agent adapter."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from supervisor.seams.interfaces.subagent_transport import (
    CAP_BACKGROUND,
    CAP_FORK,
    CAP_SPAWN,
)
from supervisor.seams.core.types import (
    ChildInfo,
    JobHandle,
    SubagentRequest,
    SubagentRunResult,
)


class AcpSubagentProvider:
    """Provider: remote subagent via HTTP (ACP-shaped wire, harness-neutral).

    Expects agent endpoint:
      POST {base}/v1/run  → { text, child_id?, status? }
      POST {base}/v1/initialize  → { protocolVersion } (optional health)
    """

    name = "acp"
    capabilities = frozenset({CAP_SPAWN, CAP_FORK, CAP_BACKGROUND})

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 120.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url required")
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._closed = False
        self._children: dict[str, ChildInfo] = {}
        self.started = False

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            f"{self._base}{path}",
            json=body,
            headers=self._headers(),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"ACP HTTP {resp.status_code}: {resp.text}")
        data = resp.json()
        if not isinstance(data, dict):
            return {"text": str(data)}
        return data

    def initialize(self) -> dict[str, Any]:
        """Optional ACP handshake — protocol version exchange."""
        try:
            return self._post(
                "/v1/initialize",
                {"protocolVersion": "1", "capabilities": {}},
            )
        except Exception:
            return {"protocolVersion": "1", "skipped": True}

    def start_one_shot(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        self.initialize()
        data = self._post(
            "/v1/run",
            {
                "prompt": request.prompt,
                "description": request.description,
                "mode": request.mode or "spawn",
            },
        )
        child_id = str(data.get("child_id") or f"acp-{uuid.uuid4().hex[:10]}")
        self._children[child_id] = ChildInfo(
            id=child_id,
            status="done",
            description=request.description,
        )
        return SubagentRunResult(
            text=str(data.get("text") or ""),
            child_id=child_id,
            status=str(data.get("status") or "ok"),
            meta={"transport": "acp"},
        )

    def start_fork(self, request: SubagentRequest) -> SubagentRunResult:
        self.started = True
        self.initialize()
        data = self._post(
            "/v1/run",
            {
                "prompt": request.prompt,
                "description": request.description,
                "mode": "fork",
                "run_id": request.run_id,
            },
        )
        child_id = str(data.get("child_id") or f"acp-fork-{uuid.uuid4().hex[:10]}")
        return SubagentRunResult(
            text=str(data.get("text") or ""),
            child_id=child_id,
            status=str(data.get("status") or "ok"),
            meta={"transport": "acp", "mode": "fork"},
        )

    def start_background(self, request: SubagentRequest) -> JobHandle:
        self.started = True
        data = self._post(
            "/v1/jobs",
            {"prompt": request.prompt, "description": request.description},
        )
        job_id = str(data.get("job_id") or f"acp-job-{uuid.uuid4().hex[:10]}")
        return JobHandle(job_id=job_id)

    def send_message(self, child_id: str, text: str) -> None:
        self._post(f"/v1/children/{child_id}/message", {"content": text})

    def interrupt(self, child_id: str) -> None:
        self._post(f"/v1/children/{child_id}/interrupt", {})

    def list_children(self, parent_id: str | None = None) -> list[ChildInfo]:
        _ = parent_id
        return list(self._children.values())

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True
