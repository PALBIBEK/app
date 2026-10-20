"""D5 §6 C — deploy/ops static acceptance (compose, ESO, contract auth language)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]  # …/prime-agent-study-main


def test_staging_compose_nonempty_gateway_token_and_health() -> None:
    """§6 C: staging compose has non-empty gateway token pattern + healthcheck."""
    path = REPO / "deploy" / "staging" / "docker-compose.staging.yml"
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert "MODEL_GATEWAY_TOKEN" in text
    # Must not hard-code empty string as the only staging token.
    assert 'MODEL_GATEWAY_TOKEN: ""' not in text
    assert "staging-gateway-token" in text or "${MODEL_GATEWAY_TOKEN" in text
    assert "healthcheck" in text
    assert "service_healthy" in text or "condition: service_healthy" in text


def test_eso_includes_gateway_token() -> None:
    """§6 C: ESO template includes gateway tokens."""
    path = REPO / "deploy" / "k8s" / "external-secret-gateway.yaml"
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert "MODEL_GATEWAY_TOKEN" in text


def test_eso_includes_vendor_keys_for_configured_routes() -> None:
    """§6 C: ESO includes vendor keys (OPENROUTER / OPENAI) for non-echo routes."""
    path = REPO / "deploy" / "k8s" / "external-secret-gateway.yaml"
    text = path.read_text(encoding="utf-8")
    has_vendor = ("OPENROUTER_API_KEY" in text) or ("OPENAI_API_KEY" in text)
    assert has_vendor, (
        "ESO must provision vendor API keys into gateway secrets "
        "(OPENROUTER_API_KEY and/or OPENAI_API_KEY)"
    )


@pytest.mark.xfail(
    strict=False,
    reason="D5 High: durable multi-replica VK store — not MVP; memory store OK for single replica",
)
def test_durable_vk_store_interface_for_multi_replica() -> None:
    """Optional High: durable VK store beyond MemoryVirtualKeyStore for multi-replica."""
    from model_gateway import virtual_keys as vk

    # Acceptance: a non-memory durable store type exists and is selectable.
    names = [n for n in dir(vk) if "Store" in n or "Postgres" in n or "Redis" in n]
    durable = [n for n in names if n not in ("VirtualKeyStore", "MemoryVirtualKeyStore")]
    assert durable, "expected Postgres/Redis (or similar) VirtualKeyStore for multi-replica"
