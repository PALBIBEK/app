"""Capability seams: ToolProvider, PromptAssembler, SubagentTransport, ModelGatewayClient."""

from __future__ import annotations

from .errors import (
    ProviderConflict,
    ProviderMissing,
    SeamError,
    UnsupportedCapability,
)
from .factory import Seams, build_default_seams, wire_agent
from .model_gateway import ModelGatewayClient
from .prompt_assembler import AssembledPrompt, PromptAssembler, PromptBindings
from .subagent_transport import (
    ChildHandle,
    ChildInfo,
    JobHandle,
    NamedProviderRegistry,
    SubagentRequest,
    SubagentTransport,
    SubagentTransportImpl,
)
from .tool_provider import ToolDescriptor, ToolProvider

__all__ = [
    "AssembledPrompt",
    "ChildHandle",
    "ChildInfo",
    "JobHandle",
    "ModelGatewayClient",
    "NamedProviderRegistry",
    "PromptAssembler",
    "PromptBindings",
    "ProviderConflict",
    "ProviderMissing",
    "SeamError",
    "Seams",
    "SubagentRequest",
    "SubagentTransport",
    "SubagentTransportImpl",
    "ToolDescriptor",
    "ToolProvider",
    "UnsupportedCapability",
    "build_default_seams",
    "wire_agent",
]
