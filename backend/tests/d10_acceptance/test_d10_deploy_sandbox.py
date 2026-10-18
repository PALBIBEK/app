"""D10 — BFF/deploy sandbox provider defaults + MT gate (static + mock)."""

from __future__ import annotations

import re
from pathlib import Path

AI_REPO = Path(__file__).resolve().parents[3] / "prime-agent-study-main"
ROOT_COMPOSE = AI_REPO / "docker-compose.yml"
STAGING_COMPOSE = AI_REPO / "deploy" / "staging" / "docker-compose.staging.yml"


def _supervisor_env_block(text: str, service: str = "supervisor") -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\s*\n(.*?)(?=^  [a-z0-9_-]+:\s*$|\Z)",
        text,
    )
    assert match, f"could not parse {service} block"
    return match.group(1)


def test_root_compose_supervisor_sandbox_provider_local_docker() -> None:
    block = _supervisor_env_block(ROOT_COMPOSE.read_text(encoding="utf-8"))
    assert re.search(
        r"SANDBOX_PROVIDER:\s*\$\{SANDBOX_PROVIDER:-local_docker\}",
        block,
    ), "root compose supervisor must default SANDBOX_PROVIDER to local_docker"


def test_staging_compose_defaults_local_docker_not_none() -> None:
    text = STAGING_COMPOSE.read_text(encoding="utf-8")
    assert text.count("SANDBOX_PROVIDER: ${SANDBOX_PROVIDER:-local_docker}") >= 2
    assert "SANDBOX_PROVIDER:-none" not in text


def test_shipped_compositions_pin_sandbox_profile() -> None:
    import json

    harness = AI_REPO / "harness"
    for name in ("coding-v1.json", "githi-tools-v1.json"):
        data = json.loads((harness / "compositions" / name).read_text(encoding="utf-8"))
        sp = data.get("sandbox_profile")
        assert sp is not None, f"{name} must pin sandbox_profile"
        assert sp["id"] in ("coding-docker-v1", "githi-docker-v1")
        assert sp["digest"].startswith("sha256:")


def test_harness_bind_service_pins_match_shipped_digests() -> None:
    from prime_agent.services.harness_bind_service import SHIPPED_COMPOSITION_PINS

    assert SHIPPED_COMPOSITION_PINS["coding-v1"]["composition_digest"].startswith("sha256:")
    assert SHIPPED_COMPOSITION_PINS["githi-tools-v1"]["composition_digest"].startswith("sha256:")


def test_multi_tenant_gate_refuses_local_docker() -> None:
    import sys

    sup_src = AI_REPO / "services" / "supervisor" / "src"
    sys.path.insert(0, str(sup_src))
    try:
        from supervisor.sandbox.gate import assert_multi_tenant_sandbox_provider

        import pytest

        with pytest.raises(ValueError, match="local_docker"):
            assert_multi_tenant_sandbox_provider("local_docker", multi_tenant_strict=True)
    finally:
        if str(sup_src) in sys.path:
            sys.path.remove(str(sup_src))

def test_e2b_egress_documented_for_operators() -> None:
    """B2: E2B network/egress expectations documented for multi-tenant path."""
    contract = AI_REPO / "contracts" / "sandbox_profile.md"
    deploy = AI_REPO / "deploy" / "README.md"
    e2b_src = AI_REPO / "services" / "supervisor" / "src" / "supervisor" / "sandbox" / "e2b.py"
    for path in (contract, deploy, e2b_src):
        text = path.read_text(encoding="utf-8")
        assert "e2b" in text.lower()
        assert "egress" in text.lower() or "network" in text.lower()
    contract_text = contract.read_text(encoding="utf-8")
    assert "SaaS-managed egress" in contract_text or "SaaS-managed" in contract_text
    deploy_text = deploy.read_text(encoding="utf-8")
    assert "E2B network/egress" in deploy_text or "E2B" in deploy_text
