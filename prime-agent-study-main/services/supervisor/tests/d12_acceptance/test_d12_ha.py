"""D12 §8 T9–T13 — high availability + drain gates (blind acceptance)."""

from __future__ import annotations

import asyncio
import re

import pytest

from supervisor.policy.approvals import ApprovalBroker
from tests.d12_acceptance.conftest import (
    AI_ROOT,
    HEADERS,
    create_session,
    new_command_id,
    wait_for_journal,
)


@pytest.mark.asyncio
async def test_t9_cold_resume_peer_can_message_from_shared_journal(
    dual_peer_stacks,
) -> None:
    """T9: After owner gone, peer messages from shared journal SoT."""
    a = dual_peer_stacks["a"]
    b = dual_peer_stacks["b"]

    created = await create_session(a["client"])
    sid = created["session_id"]

    first = await a["client"].post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS,
        json={"text": "owner turn", "command_id": new_command_id("owner")},
    )
    assert first.status_code == 202, first.text
    await wait_for_journal(a["reader"], sid, min_events=1, require_types={"RunEnd"})

    # Owner process gone: drop warm registry (simulates pod death; journal remains).
    reg = a["manager"]._registry
    if reg.get(sid) is not None:
        reg.remove(sid)

    # Peer B never had the warm runtime — cold resume from shared journal.
    assert b["manager"]._registry.get(sid) is None

    peer = await b["client"].post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS,
        json={"text": "peer cold resume", "command_id": new_command_id("peer")},
    )
    assert peer.status_code == 202, (
        f"peer must cold-resume message from journal SoT, got "
        f"{peer.status_code}: {peer.text}"
    )
    await wait_for_journal(b["reader"], sid, min_events=2, require_types={"RunEnd"})


@pytest.mark.asyncio
async def test_t10_wrong_replica_live_hub_is_process_local(
    dual_peer_stacks,
) -> None:
    """T10 runtime: LiveHub fan-out is process-local — peer misses warm live unless bus/sticky."""
    a = dual_peer_stacks["a"]
    b = dual_peer_stacks["b"]

    created = await create_session(a["client"])
    sid = created["session_id"]

    # Peer B subscribes to its own process-local live hub.
    sub_b = b["manager"].subscribe_live(sid)
    try:
        msg = await a["client"].post(
            f"/v1/sessions/{sid}/messages",
            headers=HEADERS,
            json={"text": "live on a", "command_id": new_command_id("live-a")},
        )
        assert msg.status_code == 202, msg.text
        await wait_for_journal(a["reader"], sid, min_events=1, require_types={"RunEnd"})

        # Drain peer queue briefly — warm publish on A must not appear on B.
        got = []
        try:
            async with asyncio.timeout(0.3):
                while True:
                    item = await sub_b.queue.get()
                    got.append(item)
        except TimeoutError:
            pass

        staging = (
            AI_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
        ).read_text(encoding="utf-8").lower()
        cold_or_sticky = (
            "cold resume" in staging
            or "cold-resume" in staging
            or "sticky" in staging
            or "per-replica" in staging
        )
        if got:
            # Shared live bus would deliver — then sticky/bus must be documented.
            assert cold_or_sticky or "live bus" in staging or "shared live" in staging, (
                "cross-replica live delivery requires documented sticky/bus policy"
            )
        else:
            # Expected today: process-local miss — docs must not claim silent warm RR.
            assert cold_or_sticky, (
                "wrong-replica live miss requires documented cold-resume/sticky policy"
            )
    finally:
        b["manager"].unsubscribe_live(sid, sub_b)


def dual_manager_from(stack: dict):
    """Second SessionManager on the same DB (peer sweeper)."""
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry
    from tests.support.session_env import make_catalog

    factory = stack["factory"]
    tmp_path = stack["tmp_path"]
    blobs = tmp_path / "blobs"
    return SessionManager(
        session_factory=factory,
        writer=JournalWriter(factory, blob_dir=blobs),
        reader=JournalReader(factory, blob_dir=blobs),
        registry=SessionRegistry(),
        catalog=make_catalog(),
        approvals=ApprovalBroker(factory),
    )
