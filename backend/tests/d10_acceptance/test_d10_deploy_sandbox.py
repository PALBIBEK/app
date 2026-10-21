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
