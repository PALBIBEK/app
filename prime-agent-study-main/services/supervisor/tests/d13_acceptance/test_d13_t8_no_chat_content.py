"""D13 §8 T8 — chat content must never appear in span attributes."""

from __future__ import annotations

import pytest

from supervisor.agent.events import MessageEnd, RunEnd
from tests.d13_acceptance.conftest import (
    HEADERS,
    create_session,
    new_command_id,
    span_attr_blob,
    w3c_traceparent,
    wait_for_journal,
)

TRACE_ID = "e1b2c3d4e5f60718293a4b5c6d7e8f95"
SECRET = "D13-T8-PII-CHAT-SECRET-do-not-span-xx42"


@pytest.mark.asyncio
async def test_t8_chat_content_not_in_span_attributes(supervisor_stack) -> None:
    """T8: User/assistant chat bodies must not leak into SpanRecord attributes or names."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    sink = supervisor_stack["sink"]

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers={**HEADERS, "traceparent": w3c_traceparent(TRACE_ID)},
        json={"text": SECRET, "command_id": new_command_id("t8")},
    )
    assert resp.status_code in (200, 202), resp.text

    events = await wait_for_journal(
        reader,
        sid,
        min_events=1,
        require_types={"RunEnd"},
        timeout_secs=5.0,
    )
    assert any(isinstance(e.payload, RunEnd) for e in events)
    # Journal may retain content (SoT); spans must not.
    msgs = [e.payload for e in events if isinstance(e.payload, MessageEnd)]
    # Echo may put user text into tool args — still must not land on span attrs as content bodies.
    assert sink.spans, "expected projected spans to redact against"

    blob = span_attr_blob(
        [s for s in sink.spans if s.yal_session_id == sid or s.yal_run_id == TRACE_ID]
    )
    assert SECRET not in blob, (
        f"chat content leaked into span attributes/names: {SECRET!r} found in {blob[:500]}"
    )

    # Chat span must exist without content keys.
    chat_spans = [
        s
        for s in sink.spans
        if s.name == "chat"
        or (s.attributes or {}).get("gen_ai.operation.name") == "chat"
    ]
    if msgs:
        assert chat_spans, "MessageEnd projected → expect chat span"
    for s in chat_spans:
        attrs = s.attributes or {}
        forbidden = (
            "gen_ai.prompt",
            "gen_ai.completion",
            "content",
            "message",
            "text",
            "body",
            "user_message",
            "assistant_message",
        )
        for key in forbidden:
            assert key not in attrs, f"forbidden chat attr {key!r} on span {s.name!r}"
            # also block nested-looking keys
        for key in attrs:
            assert "content" not in str(key).lower() or key.startswith("yal."), (
                f"content-like attribute key forbidden: {key!r}"
            )
            val = attrs[key]
            if isinstance(val, str):
                assert SECRET not in val
