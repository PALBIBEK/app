"""D5 §6 B / T8 — stub usage with cost moves session_index.total_cost; budget can trip."""

from __future__ import annotations

import uuid

import pytest

from supervisor.agent.events import Usage
from supervisor.db.engine import create_all, make_engine, session_factory
from supervisor.db.models import SessionIndexRow, SessionRow
from supervisor.harness import BudgetSpec
from supervisor.policy.budget import BudgetTracker


@pytest.mark.asyncio
async def test_t8_stub_usage_increases_session_index_total_cost() -> None:
    """T8: usage carrying cost_total increases session_index.total_cost."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    factory = session_factory(engine)
    session_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(
            SessionRow(
                id=session_id,
                tenant_id="t-d5",
                runtime="githi",
                harness_version="coding-v1",
            )
        )
        db.add(SessionIndexRow(session_id=session_id, total_cost=0.0))
        await db.commit()

    tracker = BudgetTracker(factory, limits=BudgetSpec(max_cost_per_session=10.0))
    tracker.begin_turn(session_id)
    state = await tracker.record(
        session_id,
        Usage(
            input_tokens=10,
            output_tokens=5,
            cache_read=0,
            cache_write=0,
            cost_total=0.37,
        ),
    )
    assert state.cost_total >= 0.37 - 1e-9

    async with factory() as db:
        idx = await db.get(SessionIndexRow, session_id)
        assert idx is not None
        assert float(idx.total_cost) >= 0.37 - 1e-9

    await engine.dispose()


@pytest.mark.asyncio
async def test_t8_budget_can_trip_on_accumulated_cost() -> None:
    """§6 B: supervisor budget can trip once gateway cost reaches session_index."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    factory = session_factory(engine)
    session_id = str(uuid.uuid4())

    async with factory() as db:
        db.add(
            SessionRow(
                id=session_id,
                tenant_id="t-d5",
                runtime="githi",
                harness_version="coding-v1",
            )
        )
        db.add(SessionIndexRow(session_id=session_id, total_cost=0.0))
        await db.commit()

    tracker = BudgetTracker(factory, limits=BudgetSpec(max_cost_per_session=0.05))
    tracker.configure(session_id, BudgetSpec(max_cost_per_session=0.05))
    tracker.begin_turn(session_id)
    state = await tracker.record(
        session_id,
        Usage(1, 1, 0, 0, cost_total=0.10),
    )
    assert state.exceeded is True
    assert state.reason

    # check() before next turn must also see the overage.
    check = await tracker.check(session_id)
    assert check.exceeded is True

    await engine.dispose()


def test_githi_usage_mapping_reads_cost_total_or_cost() -> None:
    """Gateway → githi: cost_total / cost on usage dict become Usage.cost_total."""
    from supervisor.agent.runtimes.githi.loop import AgentLoop
    from supervisor.seams.types import ChatMessage, Completion

    completion = Completion(
        message=ChatMessage(role="assistant", content="ok"),
        model="githi",
        finish_reason="stop",
        usage={
            "prompt_tokens": 4,
            "completion_tokens": 2,
            "cost_total": 0.12,
        },
    )
    usage = AgentLoop._usage_from_completion(completion)
    assert usage.cost_total == pytest.approx(0.12)

    completion2 = Completion(
        message=ChatMessage(role="assistant", content="ok"),
        usage={"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.05},
    )
    usage2 = AgentLoop._usage_from_completion(completion2)
    assert usage2.cost_total == pytest.approx(0.05)
