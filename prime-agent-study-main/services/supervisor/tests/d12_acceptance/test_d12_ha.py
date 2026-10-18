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
    reg = a["manager"]._registry  # noqa: SLF001
    if reg.get(sid) is not None:
        reg.remove(sid)

    # Peer B never had the warm runtime — cold resume from shared journal.
    assert b["manager"]._registry.get(sid) is None  # noqa: SLF001

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
async def test_t10_ha_mode_documented_not_silent_round_robin_warm() -> None:
    """T10: Cold-resume-only / sticky / replicas=1 documented; no warm-safe RR claim."""
    staging = AI_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
    cold_doc = AI_ROOT / "docs" / "cold-resume-slo.md"
    session_scale = (
        AI_ROOT
        / "development"
        / "ai-backend-production-grade"
        / "SESSION-AT-SCALE.md"
    )
    d12 = (
        AI_ROOT
        / "development"
        / "ai-backend-production-grade"
        / "D12.md"
    )

    assert staging.is_file(), f"missing staging compose: {staging}"
    texts: list[tuple[str, str]] = []
    for path in (staging, cold_doc, session_scale, d12):
        if path.is_file():
            texts.append((path.name, path.read_text(encoding="utf-8")))

    combined = "\n".join(t for _, t in texts).lower()

    mentions_policy = (
        "replicas=1" in combined
        or "replica=1" in combined
        or "sticky" in combined
        or "cold resume" in combined
        or "cold-resume" in combined
        or "session affinity" in combined
        or "shared live bus" in combined
        or "live bus" in combined
    )
    assert mentions_policy, (
        "HA mode must document replicas=1 OR sticky OR cold-resume OR shared live bus"
    )

    # Fail if docs claim round-robin is warm-safe. Allow "forbidden/never/no silent RR".
    for name, raw in texts:
        low = raw.lower()
        for m in re.finditer(r"round[\s-]?robin", low):
            start = max(0, m.start() - 48)
            end = min(len(low), m.end() + 80)
            window = low[start:end]
            if any(
                w in window
                for w in (
                    "forbidden",
                    "never",
                    "do not",
                    "don't",
                    "no silent",
                    "not ",
                    "unsafe",
                    "avoid",
                )
            ):
                continue
            if any(
                w in window
                for w in (
                    "warm-safe",
                    "warm safe",
                    "sufficient for warm",
                    "enough for warm",
                    "sticky not required",
                )
            ):
                pytest.fail(
                    f"{name} claims round-robin as warm-safe (forbidden):\n{window!r}"
                )

    # Staging compose must not silently imply multi-replica warm without caveats.
    staging_text = staging.read_text(encoding="utf-8").lower()
    if "supervisor-a" in staging_text and "supervisor-b" in staging_text:
        assert (
            "sticky" in staging_text
            or "cold resume" in staging_text
            or "cold-resume" in staging_text
            or "per-replica" in staging_text
            or "warm" in staging_text
        ), (
            "dual supervisor staging must document sticky OR cold-resume "
            "(not silent multi-replica warm)"
        )


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
        except (TimeoutError, asyncio.TimeoutError):
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
