"""Attach a minted virtual-key gateway to RuntimeSeams (composition helper)."""

from __future__ import annotations

import os
from dataclasses import replace

from supervisor.agent.seams import RuntimeSeams
from supervisor.seams.core.factory import build_runtime_seams
from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient


def attach_minted_gateway(
    seams: RuntimeSeams | None,
    *,
    token: str,
    model: str | None,
    base_url: str | None = None,
) -> RuntimeSeams | None:
    """Wire HttpModelGatewayClient with a session virtual key (not provider secrets)."""
    base = (base_url or os.environ.get("GATEWAY_BASE_URL") or "").strip()
    if not base:
        return seams
    gateway = HttpModelGatewayClient(
        base_url=base, token=token, default_model=model
    )
    if seams is None:
        return build_runtime_seams(model_gateway=gateway)
    return replace(seams, model_gateway=gateway)
