"""One transport per wire protocol; a model's :class:`~supervisor.models.spec.WireApi` picks it."""

from __future__ import annotations

from collections.abc import Mapping

from supervisor.models.spec import WireApi
from supervisor.models.transports.anthropic import call_messages
from supervisor.models.transports.base import (
    AssistantTurn,
    ModelCallError,
    RequestOptions,
    Transport,
    WireCall,
)
from supervisor.models.transports.completions import call_completions
from supervisor.models.transports.responses import call_responses

TRANSPORTS: Mapping[WireApi, Transport] = {
    WireApi.ANTHROPIC_MESSAGES: call_messages,
    WireApi.OPENAI_RESPONSES: call_responses,
    WireApi.OPENAI_COMPLETIONS: call_completions,
}

__all__ = [
    "TRANSPORTS",
    "AssistantTurn",
    "ModelCallError",
    "RequestOptions",
    "Transport",
    "WireCall",
]
