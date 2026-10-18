"""D12 §8 T9–T13 — high availability + drain gates (blind acceptance)."""

from __future__ import annotations

import asyncio
import re
import time

import pytest

from supervisor.agent.events import ApprovalRequired
from supervisor.policy.approvals import ApprovalBroker
from tests.d12_acceptance.conftest import (
    AI_ROOT,
    HEADERS,
    create_session,
    new_command_id,
    resolve_lab_provider,
    spawn_body,
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



@pytest.mark.asyncio
async def test_t11_respond_ui_missing_runtime_not_silent_skip(
    dual_peer_stacks,
) -> None:
    """T11: respond_ui when runtime missing on this process — no silent success."""
    a = dual_peer_stacks["a"]
    b = dual_peer_stacks["b"]

    created = await create_session(a["client"])
    sid = created["session_id"]

    # Park an approval with harness_request_id (UI reply required when warm).
    await a["manager"]._handle_approval_required(  # noqa: SLF001
        sid,
        "turn-t11",
        ApprovalRequired(
            approval_id="",
            prompt="allow?",
            options=["yes", "no"],
            timeout_s=300,
            harness_request_id="ui-req-d12-t11",
        ),
    )
    pending = await a["manager"].pending_approvals(sid)
    assert pending, "expected pending approval"
    aid = pending[0].approval_id

    # Owner warm runtime gone / peer has no registry entry.
    if a["manager"]._registry.get(sid) is not None:  # noqa: SLF001
        a["manager"]._registry.remove(sid)  # noqa: SLF001
    assert b["manager"]._registry.get(sid) is None  # noqa: SLF001

    resp = await b["client"].post(
        f"/v1/approvals/{aid}",
        headers=HEADERS,
        json={"choice": "yes"},
    )

    # Must not look like full UI-notified success with no ownership signal.
    if resp.status_code == 200:
        body = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        text = resp.text.lower()
        signaled = (
            body.get("queued") is True
            or body.get("forwarded") is True
            or body.get("owner_required") is True
            or body.get("ui_delivered") is False
            or "queued" in text
            or "forward" in text
            or "owner" in text
            or "sticky" in text
            or "not delivered" in text
        )
        assert signaled, (
            "peer respond_ui must not silently succeed as if UI notified — "
            f"need 409/owner-required/queue/forward signal, got {resp.status_code} {resp.text}"
        )
    else:
        assert resp.status_code in (409, 503), (
            f"peer without runtime must 409/503 with owner signal, got "
            f"{resp.status_code}: {resp.text}"
        )
        detail = resp.text.lower()
        assert (
            "owner" in detail
            or "runtime" in detail
            or "sticky" in detail
            or "queue" in detail
            or "forward" in detail
            or "respond_ui" in detail
            or "warm" in detail
            or "replica" in detail
        ), f"error must explain owner/UI delivery: {resp.text}"


@pytest.mark.asyncio
async def test_t12_drain_blocks_message_spawn_and_readyz(
    supervisor_stack,
) -> None:
    """T12: While draining — POST message + subagent spawn → 503; /readyz 503."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    state.draining = True

    msg = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS,
        json={"text": "blocked", "command_id": new_command_id("drain-msg")},
    )
    assert msg.status_code == 503, msg.text

    spawn = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        headers=HEADERS,
        json=spawn_body(provider=provider, prompt="drain spawn"),
    )
    assert spawn.status_code == 503, (
        f"subagent spawn must refuse while draining, got {spawn.status_code}: {spawn.text}"
    )

    ready = await client.get("/readyz")
    assert ready.status_code == 503, ready.text
    body = ready.json()
    assert body.get("reason") == "draining" or "drain" in str(body).lower()


@pytest.mark.asyncio
async def test_t12_drain_blocks_subagent_mutators(supervisor_stack) -> None:
    """Drain gate on subagent mutators (fork/background/continuable/message/interrupt)."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    # Seed a child while healthy (best-effort) for message/interrupt targets.
    child_id = "child-drain-seed"
    seed = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        headers=HEADERS,
        json=spawn_body(provider=provider, prompt="seed"),
    )
    if seed.status_code == 200:
        child_id = seed.json().get("child_session_id") or child_id

    state.draining = True

    mutators = [
        (
            "POST",
            f"/v1/sessions/{sid}/subagents/fork",
            spawn_body(provider=provider, prompt="fork"),
        ),
        (
            "POST",
            f"/v1/sessions/{sid}/subagents/background",
            spawn_body(provider=provider, prompt="bg"),
        ),
        (
            "POST",
            f"/v1/sessions/{sid}/subagents/continuable",
            spawn_body(provider=provider, prompt="cont"),
        ),
        (
            "POST",
            f"/v1/sessions/{sid}/children/{child_id}/message",
            {"text": "hi"},
        ),
        (
            "POST",
            f"/v1/sessions/{sid}/children/{child_id}/interrupt",
            None,
        ),
        (
            "POST",
            f"/v1/sessions/{sid}/abort",
            None,
        ),
    ]

    for method, path, body in mutators:
        if method == "POST":
            r = await client.post(path, headers=HEADERS, json=body)
        else:
            r = await client.request(method, path, headers=HEADERS)
        assert r.status_code == 503, (
            f"draining must 503 {path}, got {r.status_code}: {r.text}"
        )


@pytest.mark.asyncio
async def test_t13_two_concurrent_sweepers_lease_single_expiry(
    supervisor_stack,
) -> None:
    """T13: Two concurrent expire_due/sweepers — with lease, at most one expires."""
    manager = supervisor_stack["manager"]
    client = supervisor_stack["client"]

    # Production multi-replica safety requires a leader lease on the sweeper.
    lease_names = (
        "try_acquire_sweeper_lease",
        "acquire_approval_sweeper_lease",
        "run_with_approval_sweeper_lease",
        "sweeper_lease",
        "leader_lease",
        "try_acquire_lease",
    )
    lease_found = False
    for obj in (manager, manager._approvals, type(manager), ApprovalBroker):  # noqa: SLF001
        for name in lease_names:
            if hasattr(obj, name):
                lease_found = True
                break
        if lease_found:
            break
    try:
        from supervisor.policy import leader_lease as lease_mod

        for name in lease_names:
            if hasattr(lease_mod, name):
                lease_found = True
                break
    except ImportError:
        pass
    try:
        from supervisor.policy import approvals as approvals_mod

        for name in lease_names:
            if hasattr(approvals_mod, name):
                lease_found = True
                break
    except Exception:
        pass

    assert lease_found, (
        "approval sweeper must expose a leader lease API under multi-replica "
        f"(expected one of {lease_names} on manager/approvals/leader_lease)"
    )

    created = await create_session(client)
    sid = created["session_id"]

    await manager._handle_approval_required(  # noqa: SLF001
        sid,
        "turn-t13",
        ApprovalRequired(
            approval_id="",
            prompt="expire me once",
            options=["yes", "no"],
            timeout_s=0.01,
        ),
    )
    pending = await manager.pending_approvals(sid)
    assert len(pending) == 1
    aid = pending[0].approval_id

    # Ensure overdue.
    await asyncio.sleep(0.05)
    now = time.time() + 10.0

    # Two concurrent sweepers (two managers sharing DB = two replicas).
    manager_b = dual_manager_from(supervisor_stack)

    results = await asyncio.gather(
        manager.expire_overdue_approvals(now=now),
        manager_b.expire_overdue_approvals(now=now),
    )
    expired_ids = [r.approval_id for batch in results for r in batch]
    # At most one successful expiry of this approval across both sweepers.
    assert expired_ids.count(aid) <= 1, (
        f"lease must ensure single expiry, got {expired_ids!r}"
    )

    row = await manager._approvals.get(aid)  # noqa: SLF001
    assert row is not None
    assert row.status == "timed_out"
    # Exactly one timed_out transition — not double-applied side effects.
    assert expired_ids.count(aid) == 1, (
        f"exactly one sweeper should expire {aid}, got counts in {expired_ids!r}"
    )


def dual_manager_from(stack: dict):
    """Second SessionManager on the same DB (peer sweeper)."""
    from supervisor.journal.reader import JournalReader
    from supervisor.journal.writer import JournalWriter
    from supervisor.policy.approvals import ApprovalBroker
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
