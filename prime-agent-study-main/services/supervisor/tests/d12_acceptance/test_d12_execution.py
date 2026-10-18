"""D12 §8 T1–T8 — subagent execution plane (blind acceptance)."""

from __future__ import annotations

import asyncio
import re

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.agent.events import JobEnd, JobStart, SubagentEnd, SubagentStart
from supervisor.app import build_supervisor
from supervisor.db.engine import open_db
from supervisor.journal.writer import JournalWriter
from supervisor.main import create_app
from supervisor.seams.providers.acp_subagent import AcpSubagentProvider
from supervisor.seams.providers.memory_subagent import MemorySubagentProvider
from supervisor.session.recovery import ResumeState
from tests.d12_acceptance.conftest import (
    AI_ROOT,
    HARNESS_MAX_DEPTH,
    HEADERS,
    create_session,
    journal_payloads,
    resolve_lab_provider,
    spawn_body,
    wait_for_journal,
)
from tests.support.session_env import make_catalog


@pytest.mark.asyncio
async def test_t1_spawn_lab_paired_ids_match_journal_and_children(
    supervisor_stack,
) -> None:
    """T1: Spawn lab/echo → paired Start/End; HTTP child_id+run_id == journal + children."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    reader = supervisor_stack["reader"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        headers=HEADERS,
        json=spawn_body(provider=provider, prompt="paired ids"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    http_child = data.get("child_session_id") or data.get("child_id")
    http_run = data.get("run_id")
    assert http_child, "HTTP must return child_session_id / child_id"
    assert http_run, "HTTP must return run_id aligned with journal Start"

    events = await wait_for_journal(
        reader,
        sid,
        min_events=2,
        require_types={"SubagentStart", "SubagentEnd"},
    )
    starts = journal_payloads(events, SubagentStart)
    ends = journal_payloads(events, SubagentEnd)
    assert len(starts) == 1, "exactly one SubagentStart"
    assert len(ends) == 1, "exactly one SubagentEnd (paired)"
    start = starts[0]
    end = ends[0]
    assert start.run_id == end.run_id == http_run
    assert start.child_session_id == http_child
    assert end.stop_reason in ("ok", "done", "completed") or end.stop_reason == "ok"

    listed = await client.get(f"/v1/sessions/{sid}/children", headers=HEADERS)
    assert listed.status_code == 200, listed.text
    children = listed.json()
    assert len(children) >= 1
    row = next((c for c in children if c.get("id") == http_child), None)
    assert row is not None, f"GET children missing id={http_child!r}: {children}"
    assert row.get("run_id") == http_run


@pytest.mark.asyncio
async def test_t2_unsupported_persona_fail_loud_no_fake_success(
    supervisor_stack,
) -> None:
    """T2: Unsupported persona → fail-loud; no success End pretending ok."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    reader = supervisor_stack["reader"]
    provider = resolve_lab_provider(state)

    # Stock lab/memory does not advertise CAP_PERSONA.
    created = await create_session(client)
    sid = created["session_id"]

    # Track whether provider work actually ran.
    mem = MemorySubagentProvider()
    started_before = False

    # Prefer live session transport provider if present.
    fallback = getattr(state.manager, "_fallback_seams", None)
    transport = getattr(fallback, "subagent_transport", None)
    live_provider = None
    if transport is not None:
        registry = getattr(transport, "_registry", None)
        if registry is not None:
            try:
                live_provider = registry.get(provider)
            except Exception:
                live_provider = None

    target = live_provider if live_provider is not None else mem
    if hasattr(target, "started"):
        started_before = bool(target.started)

    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        headers=HEADERS,
        json=spawn_body(
            provider=provider,
            prompt="persona probe",
            persona="unsupported-persona-xyz",
        ),
    )
    assert resp.status_code in (400, 422), (
        f"unsupported persona must fail-loud, got {resp.status_code}: {resp.text}"
    )
    detail = resp.text.lower()
    assert (
        "persona" in detail
        or "unsupported" in detail
        or "capability" in detail
    ), f"error must name persona/capability: {resp.text}"

    if hasattr(target, "started") and live_provider is not None:
        assert target.started == started_before, (
            "provider must not run success path for unsupported persona"
        )

    events = await reader.read(sid, since=-1)
    starts = journal_payloads(events, SubagentStart)
    ends = journal_payloads(events, SubagentEnd)
    # Either reject before Start, or pair with non-success End — never ok-success alone.
    if starts:
        assert ends, "if Start was journaled, End must pair (error), not hang unpaired"
        for end in ends:
            assert end.stop_reason not in ("ok", "done", "completed"), (
                f"must not pretend success End: {end!r}"
            )


@pytest.mark.asyncio
async def test_t3_background_eventually_pairs_job_and_subagent_end(
    supervisor_stack,
) -> None:
    """T3: Background job eventually gets JobEnd + SubagentEnd (happy path)."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    reader = supervisor_stack["reader"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/background",
        headers=HEADERS,
        json=spawn_body(provider=provider, prompt="bg work"),
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json().get("job_id")
    assert job_id, resp.text

    # Happy path must not leave Start unpaired forever.
    deadline = asyncio.get_event_loop().time() + 5.0
    last: list = []
    while asyncio.get_event_loop().time() < deadline:
        last = await reader.read(sid, since=-1)
        jobs_end = journal_payloads(last, JobEnd)
        subs_end = journal_payloads(last, SubagentEnd)
        jobs_start = journal_payloads(last, JobStart)
        starts = journal_payloads(last, SubagentStart)
        if jobs_start and starts and jobs_end and subs_end:
            matched_job = any(j.job_id == job_id for j in jobs_end)
            if matched_job and subs_end:
                return
        await asyncio.sleep(0.05)

    jobs_end = journal_payloads(last, JobEnd)
    subs_end = journal_payloads(last, SubagentEnd)
    pytest.fail(
        f"background left unpaired: job_id={job_id!r} "
        f"JobEnd={jobs_end!r} SubagentEnd={subs_end!r} events={len(last)}"
    )


@pytest.mark.asyncio
async def test_t4_continuable_on_stock_lab_unsupported_or_real(
    supervisor_stack,
) -> None:
    """T4: Continuable on stock lab/memory → UnsupportedCapability OR real path."""
    client = supervisor_stack["client"]
    state = supervisor_stack["state"]
    reader = supervisor_stack["reader"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/continuable",
        headers=HEADERS,
        json=spawn_body(provider=provider, prompt="continue me"),
    )

    if resp.status_code in (400, 422):
        detail = resp.text.lower()
        assert (
            "continuable" in detail
            or "unsupported" in detail
            or "capability" in detail
        ), f"unsupported path must name continuable: {resp.text}"
        return

    # Real path: 200 with child handle + journal Start (continuable mode).
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("child_id"), body
    events = await wait_for_journal(
        reader, sid, min_events=1, require_types={"SubagentStart"}
    )
    starts = journal_payloads(events, SubagentStart)
    assert starts
    assert starts[0].mode == "continuable"
    assert starts[0].child_session_id == body["child_id"]


@pytest.mark.asyncio
async def test_t5_depth_exceeding_max_400_no_subagent_start(
    supervisor_stack,
) -> None:
    """T5: parent_depth/depth > harness max_depth → 400 depth code; no Start."""
    client = supervisor_stack["client"]
    reader = supervisor_stack["reader"]
    state = supervisor_stack["state"]
    provider = resolve_lab_provider(state)

    created = await create_session(client)
    sid = created["session_id"]

    over = HARNESS_MAX_DEPTH  # parent already at max → child would exceed
    resp = await client.post(
        f"/v1/sessions/{sid}/subagents/spawn",
        headers=HEADERS,
        json=spawn_body(
            provider=provider,
            prompt="too deep",
            parent_depth=over,
        ),
    )
    assert resp.status_code == 400, (
        f"over-depth must be HTTP 400, got {resp.status_code}: {resp.text}"
    )
    detail = resp.text.lower()
    assert "depth" in detail, f"400 body must carry clear depth code/message: {resp.text}"

    events = await reader.read(sid, since=-1)
    starts = journal_payloads(events, SubagentStart)
    assert not starts, f"must not journal SubagentStart on over-depth: {starts!r}"


@pytest.mark.asyncio
async def test_t6_unpaired_subagent_start_recovery_needs_review(
    supervisor_stack,
) -> None:
    """T6: Unpaired SubagentStart → Recovery NEEDS_REVIEW (blocking signal)."""
    state = supervisor_stack["state"]
    manager = supervisor_stack["manager"]
    factory = supervisor_stack["factory"]
    tmp_path = supervisor_stack["tmp_path"]
    client = supervisor_stack["client"]

    created = await create_session(client)
    sid = created["session_id"]

    # Simulate kill mid-child: durable Start with no End.
    writer = JournalWriter(factory, blob_dir=tmp_path / "blobs")
    await writer.append(
        sid,
        SubagentStart(
            run_id="run-unpaired-d12",
            child_session_id="child-unpaired-d12",
            parent_session_id=sid,
            mode="one_shot",
            provider="memory",
            label="killed mid-flight",
        ),
        turn_id=None,
        trace_id=None,
    )

    # Drop warm registry so recovery path is cold/decide.
    registry = manager._registry  # noqa: SLF001
    if registry.get(sid) is not None:
        registry.remove(sid)

    row = await manager._get_session_row(sid)  # noqa: SLF001
    decision = await manager._recovery.decide(sid, session_row=row)  # noqa: SLF001

    assert decision.state == ResumeState.NEEDS_REVIEW, (
        f"unpaired subagent start must block as NEEDS_REVIEW, got {decision.state!r}"
    )
    # Prefer explicit unpaired subagent signal when product exposes it.
    unpaired_attr = getattr(decision, "unpaired_subagent_run_ids", None)
    if unpaired_attr is not None:
        assert "run-unpaired-d12" in unpaired_attr

    # HTTP message path must also surface blocking (409), not silently proceed.
    msg = await client.post(
        f"/v1/sessions/{sid}/messages",
        headers=HEADERS,
        json={"text": "after unpaired", "command_id": "d12-unpaired-msg"},
    )
    assert msg.status_code == 409, (
        f"message after unpaired Start must block, got {msg.status_code}: {msg.text}"
    )
    body = msg.text.lower()
    assert (
        "needs_review" in body
        or "recovery" in body
        or "unpaired" in body
        or "subagent" in body
    ), f"409 must signal recovery/unpaired: {msg.text}"


@pytest.mark.asyncio
async def test_t7_acp_without_service_auth_staging_fail_loud(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7: ACP initialize/spawn without service auth in staging-like → rejected."""
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("AUTH_REQUIRED", "true")

    # Provider-level: initialize must NOT soft-skip when staging requires auth.
    provider = AcpSubagentProvider(base_url="http://acp-staging.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    provider._client = httpx.Client(transport=httpx.MockTransport(handler))
    raised = False
    result: dict | None = None
    try:
        result = provider.initialize()
    except Exception as exc:
        raised = True
        msg = str(exc).lower()
        assert (
            "401" in msg
            or "unauth" in msg
            or "auth" in msg
            or "forbidden" in msg
            or "credential" in msg
            or "token" in msg
            or "acp" in msg
        ), f"unexpected initialize error: {exc!r}"
    finally:
        provider.close()

    if not raised:
        assert isinstance(result, dict), result
        assert result.get("skipped") is not True, (
            "ACP initialize must not soft-skip auth failure in staging "
            f"(got {result!r})"
        )
        failed = (
            "error" in result
            or result.get("ok") is False
            or result.get("status") in ("error", "unauthorized", "rejected")
        )
        assert failed, (
            f"initialize without service auth must fail-loud, got {result!r}"
        )

    # HTTP: auth_required staging-like stack rejects bare (no service JWT) callers.
    secret = "d12-staging-jwt-secret-at-least-32-chars!!"
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 't7.db'}") as (_, factory):
        state = build_supervisor(
            session_factory=factory,
            catalog=make_catalog(),
            blob_dir=tmp_path / "blobs",
            auth_required=True,
            service_jwt_secret=secret,
        )
        app = create_app(state=state)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            bare = await client.post(
                "/v1/sessions",
                headers={"X-Tenant-Id": "d12-tenant"},
                json={"harness_version": "kg-v1", "runtime": "echo"},
            )
            assert bare.status_code in (401, 403), (
                f"staging auth_required must reject bare session create: "
                f"{bare.status_code} {bare.text}"
            )


@pytest.mark.asyncio
async def test_t8_provider_naming_not_long_term_or_session_memory() -> None:
    """T8: memory/lab_echo must NOT be described as long-term/session memory."""
    providers_dir = (
        AI_ROOT
        / "services"
        / "supervisor"
        / "src"
        / "supervisor"
        / "seams"
        / "providers"
    )
    candidates = [
        providers_dir / "memory_subagent.py",
        providers_dir / "lab_echo_subagent.py",
    ]
    existing = [p for p in candidates if p.is_file()]
    assert existing, f"expected lab echo provider module under {providers_dir}"

    for path in existing:
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        # Positive requirement: disclaimer that this is NOT session/LTM memory.
        has_disclaimer = (
            "not session memory" in lower
            or (
                "not related to" in lower
                and (
                    "contextmemory" in lower.replace(" ", "")
                    or "context_memory" in lower
                    or "session memory" in lower
                )
            )
            or ("lab_echo" in lower and ("stub" in lower or "echo" in lower))
            or (
                "echo" in lower
                and ("stub" in lower or "lab" in lower)
                and "not" in lower
            )
        )
        assert has_disclaimer, (
            f"{path.name} must disclaim LTM/session-memory conflation"
        )

        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if "not" in low and (
                "session memory" in low
                or "long-term" in low
                or "long term" in low
                or "ltm" in low
                or "durable" in low
            ):
                continue  # disclaimer line
            if "see" in low and (
                "contextmemory" in low.replace(" ", "") or "memory.md" in low
            ):
                continue
            if re.search(
                r"(?i)\b(is|provides|implements|stores)\b.*\b("
                r"long[\s-]?term\s+memory|session\s+memory|durable\s+ltm)\b",
                stripped,
            ):
                pytest.fail(
                    f"{path.name}:{i} describes provider as LTM/session memory: "
                    f"{stripped}"
                )

    # Contracts must not label the echo/"memory" provider as product LTM.
    for cpath in (
        AI_ROOT / "contracts" / "subagent.md",
        AI_ROOT / "contracts" / "mcp_acp.md",
    ):
        if not cpath.is_file():
            continue
        ctext = cpath.read_text(encoding="utf-8").lower()
        if re.search(
            r"(?:memory|lab_echo)\s+provider[^\n]{0,100}"
            r"(?:long[\s-]?term|session\s+memory|durable\s+ltm)",
            ctext,
        ):
            pytest.fail(f"{cpath.name} conflates echo/memory provider with LTM")
