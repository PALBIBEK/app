"""Long-term facts belong to a user and an agent, so they reach that user's next session.

Before, every fact was scoped to ``session:<id>`` (never recalled again), and
recall named the supervisor's session as a memory-service log session, which
made every recall a 404.
"""

from __future__ import annotations

from typing import Any

import pytest

from supervisor.memory.binding import long_term_agent
from supervisor.seams import MemoryInvokeContext, ToolInvokeContext
from supervisor.seams.providers.memory import durable_recall
from supervisor.seams.providers.memory.durable_recall import DurableRecallProvider
from supervisor.seams.services import resolve_memory_scope
from supervisor.tools.context import ToolContext


def test_a_fact_is_scoped_to_its_user_and_agent() -> None:
    scope = resolve_memory_scope(session_id="s1", tenant_id="t1", owner="alice", agent="my-agent")
    assert scope == {"tenant": "t1", "subject": "user:alice", "namespace": "my-agent"}


def test_a_deployment_wide_subject_never_merges_users(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_SUBJECT", "repo:shared")
    alice = resolve_memory_scope(session_id="s1", tenant_id="t1", owner="alice", agent="a")
    bob = resolve_memory_scope(session_id="s2", tenant_id="t1", owner="bob", agent="a")
    assert alice["subject"] != bob["subject"]
    no_user = resolve_memory_scope(session_id="s3", tenant_id="t1")
    assert no_user["subject"] == "repo:shared"


def test_the_agent_is_its_name_not_its_version() -> None:
    assert long_term_agent(composition_id="my-agent", harness_version="githi-v1") == "my-agent"
    assert long_term_agent(composition_id=None, harness_version="githi-v1") == "githi-v1"


def test_tools_see_the_sessions_user_and_agent() -> None:
    ctx = ToolContext.from_invoke(
        ToolInvokeContext(extra={"session_id": "s1", "tenant_id": "t1", "owner": "alice", "agent": "a"})
    )
    assert (ctx.owner, ctx.agent) == ("alice", "a")


def test_recall_asks_for_the_users_scope_and_names_no_log_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_recall(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"items": [{"content": "Alice's dog is Rex"}]}

    monkeypatch.setattr(durable_recall, "recall_memory_items", fake_recall)
    provider = DurableRecallProvider(base_url="http://memory:8092")
    ctx = MemoryInvokeContext(session_id="s9", tenant_id="t1", owner="alice", agent="my-agent")
    block = provider.recall_for_turn(ctx, user_prompt="dog?")
    assert block is not None and "Rex" in block.text
    (call,) = calls
    assert call["scope"] == {"tenant": "t1", "subject": "user:alice", "namespace": "my-agent"}
    assert "log_session_id" not in call
