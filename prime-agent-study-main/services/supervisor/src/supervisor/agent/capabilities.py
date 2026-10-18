"""Runtime capability declaration and validation."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from supervisor.agent.errors import CapabilityError


class Cap:
    """Standard capability identifiers."""
    ABORT = "abort"
    FORK = "fork"
    APPROVALS = "approvals"
    GET_STATE = "get_state"
    STREAMING = "streaming"
    COMPACTION = "compaction"
    MEMORY = "memory"
    SUBAGENTS = "subagents"
    SANDBOXED = "sandboxed"


CapabilityNames = Cap  # backwards compat

# Module-level mapping (not inside dataclass - that would make it a field)
_CAP_TO_ATTR = {
    Cap.ABORT: "supports_abort",
    Cap.FORK: "supports_fork",
    Cap.APPROVALS: "supports_approvals",
    Cap.GET_STATE: "supports_get_state",
    Cap.STREAMING: "supports_streaming",
    Cap.COMPACTION: "supports_compaction",
}


@dataclass(frozen=True)
class Capabilities:
    """Immutable capability descriptor."""
    protocol_version: str
    supports_abort: bool = True
    supports_fork: bool = False
    supports_approvals: bool = False
    supports_get_state: bool = True
    supports_streaming: bool = True
    supports_compaction: bool = False
    extras: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, Any] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        if attr := _CAP_TO_ATTR.get(capability):
            return getattr(self, attr)
        return capability in self.extras

    def with_extra(self, *capabilities: str) -> Capabilities:
        return replace(self, extras=self.extras | frozenset(capabilities))

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "supports_abort": self.supports_abort,
            "supports_fork": self.supports_fork,
            "supports_approvals": self.supports_approvals,
            "supports_get_state": self.supports_get_state,
            "supports_streaming": self.supports_streaming,
            "supports_compaction": self.supports_compaction,
            "extras": list(self.extras),
        }


@dataclass(frozen=True)
class CapabilityRequirement:
    """Specification for required capabilities."""
    capability: str
    required: bool = True
    reason: str | None = None

    def check(self, caps: Capabilities) -> bool:
        return not self.required or caps.supports(self.capability)


def require(have: Capabilities, need: Capabilities) -> None:
    """Validate that `have` satisfies `need`. Raises CapabilityError on mismatch."""
    if have.protocol_version != need.protocol_version:
        raise CapabilityError(
            message=f"Protocol version mismatch: have {have.protocol_version}, need {need.protocol_version}",
            required=f"protocol_version={need.protocol_version}",
            available=[f"protocol_version={have.protocol_version}"],
        )

    checks = [
        ("supports_abort", need.supports_abort, have.supports_abort),
        ("supports_fork", need.supports_fork, have.supports_fork),
        ("supports_approvals", need.supports_approvals, have.supports_approvals),
        ("supports_get_state", need.supports_get_state, have.supports_get_state),
        ("supports_streaming", need.supports_streaming, have.supports_streaming),
        ("supports_compaction", need.supports_compaction, have.supports_compaction),
    ]

    missing = [name for name, needed, has in checks if needed and not has]
    missing.extend(f"extra:{e}" for e in need.extras if e not in have.extras)

    if missing:
        available = [name for name, _, has in checks if has]
        available.extend(f"extra:{e}" for e in have.extras)
        raise CapabilityError(
            message=f"Missing capabilities: {', '.join(missing)}",
            required=missing[0] if len(missing) == 1 else str(missing),
            available=available,
        )


def require_capability(caps: Capabilities, capability: str, reason: str | None = None) -> None:
    """Require a single capability. Raises CapabilityError if unsupported."""
    if caps.supports(capability):
        return

    available = [c for c in _CAP_TO_ATTR if caps.supports(c)]
    available.extend(caps.extras)

    msg = f"Capability not supported: {capability}"
    raise CapabilityError(message=f"{msg} ({reason})" if reason else msg, required=capability, available=available)


# Standard profiles
MINIMAL_CAPABILITIES = Capabilities(
    protocol_version="1.0",
    supports_abort=False, supports_fork=False, supports_approvals=False,
    supports_get_state=False, supports_streaming=False, supports_compaction=False,
)

STANDARD_CAPABILITIES = Capabilities(
    protocol_version="1.0",
    supports_abort=True, supports_fork=False, supports_approvals=False,
    supports_get_state=True, supports_streaming=True, supports_compaction=False,
)

FULL_CAPABILITIES = Capabilities(
    protocol_version="1.0",
    supports_abort=True, supports_fork=True, supports_approvals=True,
    supports_get_state=True, supports_streaming=True, supports_compaction=True,
)
