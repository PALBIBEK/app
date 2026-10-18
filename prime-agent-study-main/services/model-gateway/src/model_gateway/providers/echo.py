"""Echo ChatProvider — deterministic replies for solo / CI development."""

from __future__ import annotations

import time
import uuid
from typing import Any


class EchoChatProvider:
    name = "echo"

    def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        upstream_model: str,
        stream: bool,
    ) -> dict[str, Any]:
        last_user = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                last_user = content if isinstance(content, str) else str(content)
                break
        text = f"echo:{last_user}" if last_user else "echo:ok"
        created = int(time.time())
        completion_id = f"chatcmpl-echo-{uuid.uuid4().hex[:12]}"
        if stream:
            return {
                "stream": True,
                "id": completion_id,
                "model": upstream_model,
                "created": created,
                "chunks": [text],
            }
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": upstream_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": len(text.split()),
                "total_tokens": len(text.split()),
            },
        }
