"""
Session management activities.

These activities handle persistent state for agent sessions:
- Session initialization (create database records)
- Context loading (retrieve conversation history)
- Checkpointing (save intermediate state)
- Turn persistence (finalize turn state)
- Approval management

All activities are idempotent - safe to retry on failure.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import Any

from temporalio import activity

logger = logging.getLogger(__name__)

# These will be injected at runtime
_session_store: Any = None
_approval_store: Any = None


def configure_session_activities(
    session_store: Any,
    approval_store: Any | None = None,
) -> None:
    """
    Configure session activities with dependencies.

    Called during worker initialization to inject stores.

    Args:
        session_store: SessionStore implementation.
        approval_store: ApprovalStore implementation (optional).
    """
    global _session_store, _approval_store
    _session_store = session_store
    _approval_store = approval_store


def _get_session_store() -> Any:
    """Get configured session store or raise."""
    if _session_store is None:
        raise RuntimeError(
            "Session store not configured. "
            "Call configure_session_activities() first."
        )
    return _session_store


@activity.defn
async def initialize_session(
    session_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """
    Initialize a new session in persistent store.

    Creates session record with configuration snapshot.
    Idempotent - safe to call multiple times.

    Args:
        session_id: Unique session identifier.
        config: Session configuration (SessionConfig as dict).

    Returns:
        Initialization result with session metadata.
    """
    store = _get_session_store()
    info = activity.info()

    logger.info(
        f"Initializing session: session_id={session_id}, "
        f"tenant={config.get('tenant_id')}, "
        f"workflow_id={info.workflow_id}"
    )

    # Check if already exists (idempotency)
    existing = await store.get(session_id)
    if existing:
        logger.info(f"Session already exists: {session_id}")
        return {
            "session_id": session_id,
            "status": "already_exists",
            "created_at": existing.get("created_at"),
        }

    # Create session record
    now = time.time()
    session_record = {
        "session_id": session_id,
        "tenant_id": config.get("tenant_id"),
        "agent_id": config.get("agent_id"),
        "model_id": config.get("model_id"),
        "status": "active",
        "config": config,
        "messages": [],
        "turn_count": 0,
        "total_tokens": 0,
        "created_at": now,
        "updated_at": now,
        "workflow_id": info.workflow_id,
    }

    await store.create(session_id, session_record)

    logger.info(f"Session initialized: {session_id}")
    return {
        "session_id": session_id,
        "status": "created",
        "created_at": now,
    }


@activity.defn
async def load_context(session_id: str) -> dict[str, Any]:
    """
    Load conversation context for a session.

    Retrieves message history and session metadata.

    Args:
        session_id: Session to load.

    Returns:
        Context dict with messages, tools, metadata.
    """
    store = _get_session_store()
    info = activity.info()

    logger.debug(f"Loading context: session_id={session_id}")

    session = await store.get(session_id)
    if not session:
        logger.warning(f"Session not found: {session_id}")
        return {
            "messages": [],
            "tools": [],
            "metadata": {},
        }

    # Load messages (may be paginated for large histories)
    messages = session.get("messages", [])

    # Load available tools from config
    config = session.get("config", {})
    tools_enabled = config.get("tools_enabled", [])

    # Build context
    context = {
        "messages": messages,
        "tools": tools_enabled,
        "metadata": {
            "session_id": session_id,
            "tenant_id": session.get("tenant_id"),
            "model_id": config.get("model_id"),
            "turn_count": session.get("turn_count", 0),
            "total_tokens": session.get("total_tokens", 0),
        },
    }

    logger.debug(
        f"Context loaded: messages={len(messages)}, tools={len(tools_enabled)}"
    )
    return context


@activity.defn
async def checkpoint(
    session_id: str,
    turn_id: str,
    messages: list[dict[str, Any]],
    iteration: int,
) -> dict[str, Any]:
    """
    Checkpoint intermediate turn state.

    Saves current messages and progress for crash recovery.
    Called periodically during tool execution loop.

    Args:
        session_id: Session identifier.
        turn_id: Current turn identifier.
        messages: Current message history.
        iteration: Current loop iteration.

    Returns:
        Checkpoint result with timestamp.
    """
    store = _get_session_store()

    logger.debug(
        f"Checkpointing: session={session_id}, turn={turn_id}, "
        f"iteration={iteration}, messages={len(messages)}"
    )

    now = time.time()

    # Update session with checkpoint
    await store.update(
        session_id,
        {
            "messages": messages,
            "checkpoint": {
                "turn_id": turn_id,
                "iteration": iteration,
                "timestamp": now,
            },
            "updated_at": now,
        },
    )

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "iteration": iteration,
        "timestamp": now,
    }


@activity.defn
async def persist_turn(
    session_id: str,
    turn_id: str,
    messages: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """
    Persist completed turn state.

    Saves final messages, clears checkpoint, updates metrics.

    Args:
        session_id: Session identifier.
        turn_id: Completed turn identifier.
        messages: Final message history.
        metrics: Turn metrics (tokens, iterations, etc.).

    Returns:
        Persistence result.
    """
    store = _get_session_store()

    logger.info(
        f"Persisting turn: session={session_id}, turn={turn_id}, "
        f"messages={len(messages)}, tokens={metrics.get('tokens_used')}"
    )

    now = time.time()

    # Get current session for updating counters
    session = await store.get(session_id)
    current_turn_count = session.get("turn_count", 0) if session else 0
    current_tokens = session.get("total_tokens", 0) if session else 0

    # Update session
    await store.update(
        session_id,
        {
            "messages": messages,
            "checkpoint": None,  # Clear checkpoint
            "turn_count": current_turn_count + 1,
            "total_tokens": current_tokens + metrics.get("tokens_used", 0),
            "last_turn": {
                "turn_id": turn_id,
                "iterations": metrics.get("iterations", 0),
                "tool_calls": metrics.get("tool_calls_total", 0),
                "tokens": metrics.get("tokens_used", 0),
                "stop_reason": metrics.get("stop_reason", "unknown"),
                "completed_at": now,
            },
            "updated_at": now,
        },
    )

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "persisted_at": now,
    }


@activity.defn
async def persist_approval_request(
    session_id: str,
    approval_request: dict[str, Any],
) -> dict[str, Any]:
    """
    Persist approval request for external retrieval.

    Stores approval details so external systems can query pending approvals.

    Args:
        session_id: Session identifier.
        approval_request: ApprovalRequest as dict.

    Returns:
        Persistence result with approval ID.
    """
    if _approval_store is None:
        logger.warning("Approval store not configured, skipping persistence")
        return {
            "approval_id": approval_request.get("approval_id"),
            "persisted": False,
        }

    approval_id = approval_request.get("approval_id")

    logger.info(
        f"Persisting approval request: session={session_id}, "
        f"approval={approval_id}"
    )

    now = time.time()

    await _approval_store.create(
        approval_id,
        {
            "approval_id": approval_id,
            "session_id": session_id,
            "turn_id": approval_request.get("turn_id"),
            "prompt": approval_request.get("prompt"),
            "options": approval_request.get("options"),
            "tool_name": approval_request.get("tool_name"),
            "tool_args": approval_request.get("tool_args"),
            "status": "pending",
            "requested_at": approval_request.get("requested_at", now),
            "created_at": now,
        },
    )

    return {
        "approval_id": approval_id,
        "persisted": True,
        "persisted_at": now,
    }


@activity.defn
async def resume_turn_with_approval(
    session_id: str,
    turn_id: str,
    approval_id: str,
    decision: str,
) -> dict[str, Any]:
    """
    Resume turn execution after approval received.

    Continues tool execution based on approval decision.

    Args:
        session_id: Session identifier.
        turn_id: Turn that was waiting for approval.
        approval_id: Approval request ID.
        decision: User's decision ("approve", "deny", "modify").

    Returns:
        Turn result after resumption.
    """
    store = _get_session_store()

    logger.info(
        f"Resuming turn with approval: session={session_id}, "
        f"turn={turn_id}, decision={decision}"
    )

    # Load current state
    session = await store.get(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")

    checkpoint = session.get("checkpoint")
    if not checkpoint or checkpoint.get("turn_id") != turn_id:
        logger.warning(f"No checkpoint found for turn: {turn_id}")
        return {
            "turn_id": turn_id,
            "stop_reason": "no_checkpoint",
            "output": "",
            "tool_calls_count": 0,
            "tokens_used": 0,
        }

    messages = session.get("messages", [])

    # Update approval store
    if _approval_store is not None:
        await _approval_store.update(
            approval_id,
            {
                "status": "responded",
                "decision": decision,
                "responded_at": time.time(),
            },
        )

    # Handle decision
    if decision == "deny":
        # Add denial message to context
        messages.append({
            "role": "system",
            "content": "The user denied the requested action.",
        })

        await store.update(
            session_id,
            {
                "messages": messages,
                "checkpoint": None,
                "updated_at": time.time(),
            },
        )

        return {
            "turn_id": turn_id,
            "stop_reason": "approval_denied",
            "output": "Action was denied by user.",
            "tool_calls_count": 0,
            "tokens_used": 0,
            "needs_approval": False,
        }

    # For "approve" or "modify", the turn workflow will continue
    # This activity just records the decision and returns control
    return {
        "turn_id": turn_id,
        "stop_reason": "approval_received",
        "decision": decision,
        "needs_approval": False,
        # Actual continuation happens in workflow
    }
