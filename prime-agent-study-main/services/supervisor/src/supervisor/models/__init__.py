"""Model access the way QM does it: a native registry, gateway discovery, three wire protocols.

* :mod:`~supervisor.models.spec` — what a model is (protocol, limits, prices, reasoning);
* :mod:`~supervisor.models.registry` — QM's native model list on pi-ai's templates;
* :mod:`~supervisor.models.endpoints` — gateway configuration and routing;
* :mod:`~supervisor.models.catalog` — gateway discovery and admission;
* :mod:`~supervisor.models.transports` — Anthropic Messages, OpenAI Responses, chat completions;
* :mod:`~supervisor.models.client` — the routed ``model_gateway`` seam for sessions.
"""

from __future__ import annotations

from supervisor.models.client import ModelRouter, ModelUnavailable, RoutedModelClient
from supervisor.models.endpoints import (
    GATEWAY_MODEL_PREFIX,
    GatewayConfig,
    GatewayConfigError,
    GatewayModelUnavailable,
)
from supervisor.models.service import model_router, reset_model_router, use_model_router
from supervisor.models.spec import ModelSpec, WireApi
from supervisor.models.transports import ModelCallError

__all__ = [
    "GATEWAY_MODEL_PREFIX",
    "GatewayConfig",
    "GatewayConfigError",
    "GatewayModelUnavailable",
    "ModelCallError",
    "ModelRouter",
    "ModelSpec",
    "ModelUnavailable",
    "RoutedModelClient",
    "WireApi",
    "model_router",
    "reset_model_router",
    "use_model_router",
]
