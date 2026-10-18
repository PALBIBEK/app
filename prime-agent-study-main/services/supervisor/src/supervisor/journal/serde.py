"""Serialize/deserialize AgentEvent for journal persistence.

This module handles the conversion between AgentEvent dataclasses and
JSON-serializable dictionaries for storage in the events table.

Key design decisions:
- Events are stored with a `_type` discriminator field
- Nested dataclasses (e.g., Usage) are handled explicitly
- Unknown event types raise descriptive errors
- Encoding is deterministic for consistent hashing
"""

from __future__ import annotations

import dataclasses
from typing import Any, TypeVar

from supervisor.agent.events import (
    AgentEvent,
    ApprovalRequired,
    ApprovalResolved,
    CompactionEnd,
    CompactionStart,
    ErrorEvent,
    JobEnd,
    JobStart,
    MemoryRecall,
    MessageEnd,
    RunEnd,
    RunStart,
    Settled,
    SubagentEnd,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    Usage,
)


class SerializationError(Exception):
    """Failed to serialize an AgentEvent."""

    def __init__(self, event_type: str, reason: str) -> None:
        super().__init__(f"cannot serialize {event_type}: {reason}")
        self.event_type = event_type
        self.reason = reason


class DeserializationError(Exception):
    """Failed to deserialize an AgentEvent from stored data."""

    def __init__(self, reason: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(f"cannot deserialize event: {reason}")
        self.reason = reason
        self.data = data


# Type name → Event class mapping
_EVENT_TYPES: dict[str, type[AgentEvent]] = {
    "RunStart": RunStart,
    "RunEnd": RunEnd,
    "TurnStart": TurnStart,
    "TurnEnd": TurnEnd,
    "MessageEnd": MessageEnd,
    "ToolStart": ToolStart,
    "ToolEnd": ToolEnd,
    "Settled": Settled,
    "ErrorEvent": ErrorEvent,
    "ApprovalRequired": ApprovalRequired,
    "ApprovalResolved": ApprovalResolved,
    "SubagentStart": SubagentStart,
    "SubagentEnd": SubagentEnd,
    "JobStart": JobStart,
    "JobEnd": JobEnd,
    "CompactionStart": CompactionStart,
    "CompactionEnd": CompactionEnd,
    "MemoryRecall": MemoryRecall,
}

# Reverse mapping for validation
_TYPE_NAMES: dict[type[AgentEvent], str] = {v: k for k, v in _EVENT_TYPES.items()}

T = TypeVar("T", bound=AgentEvent)


def event_type_name(event: AgentEvent) -> str:
    """Get the serialization type name for an event.

    Args:
        event: Any AgentEvent instance.

    Returns:
        The type discriminator string (e.g., "RunStart", "ToolEnd").

    Raises:
        SerializationError: If the event type is not registered.
    """
    event_cls = type(event)
    name = _TYPE_NAMES.get(event_cls)
    if name is None:
        raise SerializationError(
            event_cls.__name__,
            f"unregistered event type; add to _EVENT_TYPES in {__name__}",
        )
    return name


# Every event class fixes its category; it is implied by ``_type``.
_DERIVED_FIELD = "category"


def encode_event(event: AgentEvent) -> dict[str, Any]:
    """Serialize an AgentEvent to a JSON-compatible dictionary.

    The output includes a `_type` field for deserialization.

    Args:
        event: Any AgentEvent instance.

    Returns:
        Dictionary suitable for JSON serialization and storage.

    Raises:
        SerializationError: If the event type is not registered.

    Example:
        >>> event = ToolStart(tool_call_id="tc-1", name="read", arguments={})
        >>> data = encode_event(event)
        >>> data["_type"]
        'ToolStart'
    """
    type_name = event_type_name(event)
    data = dataclasses.asdict(event)
    # Derived from the event type, never stored (and an Enum is not JSON).
    data.pop(_DERIVED_FIELD, None)
    data["_type"] = type_name
    return data


def decode_event(data: dict[str, Any]) -> AgentEvent:
    """Deserialize a dictionary back to an AgentEvent.

    Handles nested dataclasses (e.g., Usage in MessageEnd) automatically.

    Args:
        data: Dictionary with `_type` field and event fields.

    Returns:
        Reconstructed AgentEvent instance.

    Raises:
        DeserializationError: If `_type` is missing or unknown,
            or if required fields are missing.

    Example:
        >>> data = {"_type": "ToolStart", "tool_call_id": "tc-1", ...}
        >>> event = decode_event(data)
        >>> isinstance(event, ToolStart)
        True
    """
    if not isinstance(data, dict):
        raise DeserializationError(f"expected dict, got {type(data).__name__}")

    payload = dict(data)  # Don't mutate input
    payload.pop(_DERIVED_FIELD, None)
    type_name = payload.pop("_type", None)

    if type_name is None:
        raise DeserializationError("missing _type field", data)

    event_cls = _EVENT_TYPES.get(type_name)
    if event_cls is None:
        raise DeserializationError(
            f"unknown event type {type_name!r}; "
            f"known types: {sorted(_EVENT_TYPES.keys())}",
            data,
        )

    try:
        return _construct_event(event_cls, payload)
    except TypeError as exc:
        raise DeserializationError(
            f"failed to construct {type_name}: {exc}",
            data,
        ) from exc


def _construct_event(event_cls: type[T], payload: dict[str, Any]) -> T:
    """Construct an event, handling nested dataclasses.

    Args:
        event_cls: The event class to instantiate.
        payload: Field values (without _type).

    Returns:
        Constructed event instance.
    """
    # Handle MessageEnd.usage specially (nested dataclass)
    if event_cls is MessageEnd:
        usage_data = payload.get("usage")
        if usage_data is not None and isinstance(usage_data, dict):
            payload = dict(payload)
            payload["usage"] = Usage(**usage_data)

    return event_cls(**payload)  # type: ignore[return-value]


def is_known_event_type(type_name: str) -> bool:
    """Check if a type name corresponds to a registered event.

    Args:
        type_name: The `_type` discriminator string.

    Returns:
        True if the type is registered.
    """
    return type_name in _EVENT_TYPES


def registered_event_types() -> list[str]:
    """Get all registered event type names.

    Returns:
        Sorted list of type discriminator strings.
    """
    return sorted(_EVENT_TYPES.keys())
