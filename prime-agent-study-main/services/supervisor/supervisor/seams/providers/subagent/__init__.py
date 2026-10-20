"""SubagentTransport implementations."""

from supervisor.seams.providers.subagent.acp_subagent import AcpSubagentProvider
from supervisor.seams.providers.subagent.memory_subagent import (
    LAB_ECHO_PROVIDER,
    LEGACY_MEMORY_ALIAS,
    LabEchoSubagentProvider,
)
from supervisor.seams.providers.subagent.nested_session import NestedSessionSubagentProvider

__all__ = [
    "LAB_ECHO_PROVIDER",
    "LEGACY_MEMORY_ALIAS",
    "AcpSubagentProvider",
    "LabEchoSubagentProvider",
    "NestedSessionSubagentProvider",
]
