"""D13 §8 T1 — inbound traceparent seeds shared trace_id on journal + spans."""

from __future__ import annotations

import pytest

from supervisor.agent.events import RunEnd, RunStart
from tests.d13_acceptance.conftest import (
    HEADERS,
    create_session,
    new_command_id,
    w3c_traceparent,
    wait_for_journal,
)

TRACE_ID = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.mark.asyncio
async def test_t1_inbound_traceparent_seeds_journal_and_spans(
    supervisor_stack,
) -> None:
    """T1: Message with W3C traceparent → journal envelopes + projected spans share that id."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    sink = supervisor_stack["sink"]

    created = await create_session(client)
    sid = created["session_id"]
    secret_prompt = "D13-T1-UNIQUE-CHAT-BODY-zz99"

    resp = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers={**HEADERS, "traceparent": w3c_traceparent(TRACE_ID)},
        json={"text": secret_prompt, "command_id": new_command_id("t1")},
    )
    assert resp.status_code in (200, 202), resp.text

    events = await wait_for_journal(
        reader,
        sid,
        min_events=2,
        require_types={"RunStart", "RunEnd"},
        timeout_secs=5.0,
    )
    assert events, "expected journal events after message"
    starts = [e for e in events if isinstance(e.payload, RunStart)]
    ends = [e for e in events if isinstance(e.payload, RunEnd)]
    assert starts, "missing RunStart in journal"
    assert ends, "missing RunEnd in journal"

    for env in events:
        assert env.trace_id == TRACE_ID, (
            f"journal seq={env.seq} type={type(env.payload).__name__} "
            f"trace_id={env.trace_id!r} != inbound {TRACE_ID!r}"
        )

    # Spans must be projected (fan-out → projector) and share the same run/trace id.
    assert sink.spans, "expected SpanRecords from journal projector — tracing not soft-nooped"
    run_spans = [
        s
        for s in sink.spans
        if s.yal_session_id == sid
        or (s.attributes or {}).get("yal.session.id") == sid
    ]
    assert run_spans, f"no spans for session {sid}; got sessions={[s.yal_session_id for s in sink.spans]}"
    for s in run_spans:
        assert s.yal_run_id == TRACE_ID, (
            f"span {s.name!r} yal_run_id={s.yal_run_id!r} must equal inbound trace_id"
        )
