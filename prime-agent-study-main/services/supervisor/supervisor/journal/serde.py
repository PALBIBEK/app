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

import base64
import dataclasses
import enum
import logging
import math
from collections.abc import Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final, TypeVar

from supervisor.agent.events import (
    AgentEvent,
    ApprovalRequired,
    ApprovalResolved,
    BackgroundResults,
    CompactionEnd,
    CompactionStart,
    ErrorEvent,
    HandCheckpointed,
    HandLost,
    HandProvisioned,
    HandStopped,
    JobEnd,
    JobStart,
    MemoryContext,
    MemoryInjected,
    MemoryRecall,
    MessageEnd,
    ModelRetry,
    ProjectContext,
    RunEnd,
    RunStart,
    SubagentEnd,
    SubagentNotice,
    SubagentStart,
    ToolEnd,
    ToolStart,
    TurnEnd,
    TurnStart,
    UnknownEvent,
    Usage,
    WorkspaceRestored,
)

logger = logging.getLogger(__name__)

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


RETIRED_TYPES: Final = frozenset({"Settled"})
"""Types no build writes any more. A journal from before keeps them: they read as
``UnknownEvent`` (passed through, copied by a fork as they were), which nothing acts on.
A type is retired here, never just removed (tests/fixtures/events/README.md)."""

# Type name → Event class mapping
_EVENT_TYPES: dict[str, type[AgentEvent]] = {
    "RunStart": RunStart,
    "RunEnd": RunEnd,
    "TurnStart": TurnStart,
    "TurnEnd": TurnEnd,
    "MessageEnd": MessageEnd,
    "ToolStart": ToolStart,
    "ToolEnd": ToolEnd,
    "ErrorEvent": ErrorEvent,
    "ModelRetry": ModelRetry,
    "ProjectContext": ProjectContext,
    "WorkspaceRestored": WorkspaceRestored,
    "SubagentNotice": SubagentNotice,
    "ApprovalRequired": ApprovalRequired,
    "ApprovalResolved": ApprovalResolved,
    "SubagentStart": SubagentStart,
    "SubagentEnd": SubagentEnd,
    "JobStart": JobStart,
    "JobEnd": JobEnd,
    "CompactionStart": CompactionStart,
    "CompactionEnd": CompactionEnd,
    "MemoryRecall": MemoryRecall,
    "MemoryInjected": MemoryInjected,
    "MemoryContext": MemoryContext,
    "BackgroundResults": BackgroundResults,
    "HandProvisioned": HandProvisioned,
    "HandCheckpointed": HandCheckpointed,
    "HandLost": HandLost,
    "HandStopped": HandStopped,
}

_UNKNOWN_SEEN: set[str] = set()
"""Event types this process met that it does not know (each is logged once)."""

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
    if isinstance(event, UnknownEvent):
        return event.type_name  # kept under the type a newer build wrote it as
    event_cls = type(event)
    name = _TYPE_NAMES.get(event_cls)
    if name is None:
        raise SerializationError(
            event_cls.__name__,
            f"unregistered event type; add to _EVENT_TYPES in {__name__}",
        )
    return name


_USAGE_FIELDS: dict[type, str] = {MessageEnd: "usage", CompactionEnd: "usage", RunEnd: "total_usage"}


def type_name_of(event_cls: type) -> str:
    """The stored type name of an event class (``EventRow.type``), e.g. ``"RunStart"``."""
    name = _TYPE_NAMES.get(event_cls)
    if name is None:
        raise SerializationError(event_cls.__name__, "unregistered event type")
    return name


def jsonable(value: Any) -> Any:
    """``value`` as plain JSON: what a tool, a model or a provider hands over is stored and
    sent as it can be (a value Postgres's ``json`` or the wire would refuse failed the whole
    write, and the step's retry ran its tool again).

    Plain JSON is returned unchanged. Otherwise: a timestamp as ISO 8601; a ``Decimal`` as a
    number (as its text when it has no float); a set or tuple as a list (a set sorted when
    its items sort); text bytes as text, other bytes as ``{"base64": ...}``; NaN and the
    infinities, which JSON has no word for, as ``"NaN"``, ``"Infinity"``, ``"-Infinity"``; an
    enum as its value; a dict's keys as text; anything else as its ``str``.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    if isinstance(value, Mapping):
        return {k if isinstance(k, str) else str(jsonable(k)): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, set | frozenset):
        items = [jsonable(v) for v in value]
        try:
            return sorted(items)
        except TypeError:
            return items
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        raw = bytes(value)
        try:
            return raw.decode()
        except UnicodeDecodeError:
            return {"base64": base64.b64encode(raw).decode()}
    if isinstance(value, enum.Enum):
        return jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    return str(value)


# Every event class fixes its category; it is implied by ``_type``.
_DERIVED_FIELD = "category"


def encode_event(event: AgentEvent) -> dict[str, Any]:
    """Serialize an AgentEvent to a JSON-compatible dictionary.

    The output includes a `_type` field for deserialization.

    Args:
        event: Any AgentEvent instance.

    Returns:
        Dictionary of plain JSON (``jsonable``), for storage and the wire.

    Raises:
        SerializationError: If the event type is not registered.

    Example:
        >>> event = ToolStart(tool_call_id="tc-1", name="read", arguments={})
        >>> data = encode_event(event)
        >>> data["_type"]
        'ToolStart'
    """
    type_name = event_type_name(event)
    if isinstance(event, UnknownEvent):
        return {**jsonable(event.data), "_type": type_name}  # as it was stored
    data = dataclasses.asdict(event)
    # Derived from the event type, never stored (and an Enum is not JSON).
    data.pop(_DERIVED_FIELD, None)
    data = jsonable(data)
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
        # A newer build wrote it (a rolling deploy): kept as it is, skipped by whatever
        # does not know it, rather than failing every read of the session.
        if type_name not in _UNKNOWN_SEEN:
            _UNKNOWN_SEEN.add(type_name)
            logger.warning("journal holds event type %r this build does not know; passing it through", type_name)
        return UnknownEvent(type_name=str(type_name), data=payload)

    try:
        return _construct_event(event_cls, payload)
    except TypeError as exc:
        raise DeserializationError(
            f"failed to construct {type_name}: {exc}",
            data,
        ) from exc


def _construct_event[T: AgentEvent](event_cls: type[T], payload: dict[str, Any]) -> T:
    """Construct an event, handling nested dataclasses.

    Args:
        event_cls: The event class to instantiate.
        payload: Field values (without _type).

    Returns:
        Constructed event instance.
    """
    # Fields this build does not know were written by a newer one (a rolling deploy:
    # new workers journal before old API replicas are replaced). Ignore them rather
    # than fail every read of the session; a missing required field still fails.
    known = {f.name for f in dataclasses.fields(event_cls)}  # type: ignore[arg-type]
    if unknown := set(payload) - known:
        payload = {k: v for k, v in payload.items() if k in known}
        logger.debug("ignoring %s fields unknown to this build: %s", event_cls.__name__, sorted(unknown))

    # Usage is a nested dataclass: a reply's, a compaction's, a run's total.
    if (usage_field := _USAGE_FIELDS.get(event_cls)) is not None:
        usage_data = payload.get(usage_field)
        if usage_data is not None and isinstance(usage_data, dict):
            payload = dict(payload)
            usage_known = {f.name for f in dataclasses.fields(Usage)}
            payload[usage_field] = Usage(**{k: v for k, v in usage_data.items() if k in usage_known})

    return event_cls(**payload)  # type: ignore[return-value]


