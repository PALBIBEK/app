"""The Kubernetes templates give each service only its own secrets, and production rules.

Every service (supervisor, workers, memory service, gateway) read one shared secret, so the
provider API keys reached processes that must never hold them, and the supervisor and its
workers ran without ``ENV=production``: no service authentication required, no vault or
history key required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

K8S = Path(__file__).resolve().parents[4] / "deploy" / "k8s"
VENDOR_KEYS = {"OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}


def _docs(name: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all((K8S / name).read_text(encoding="utf-8")) if d]


def _secrets() -> dict[str, set[str]]:
    return {d["metadata"]["name"]: {item["secretKey"] for item in d["spec"]["data"]} for d in _docs("external-secret-gateway.yaml")}


def _containers() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for name in ("supervisor-deployment.yaml", "worker-deployment.yaml", "gateway-memory-deployment.yaml", "rollout-check-job.yaml"):
        for doc in _docs(name):
            template = doc.get("spec", {}).get("template") or doc.get("spec", {}).get("jobTemplate", {}).get(
                "spec", {}
            ).get("template")
            for container in (template or {}).get("spec", {}).get("containers", []):
                found[doc["metadata"]["name"]] = container
    return found


def _secret_of(container: dict[str, Any]) -> str:
    (ref,) = [e["secretRef"]["name"] for e in container.get("envFrom", [])]
    return ref


def _env(container: dict[str, Any]) -> dict[str, str]:
    return {e["name"]: e.get("value", "") for e in container.get("env", [])}


def test_only_the_gateway_holds_provider_keys() -> None:
    secrets = _secrets()
    holders = {name for name, keys in secrets.items() if keys & VENDOR_KEYS}
    assert holders == {"yal-gateway-credentials"}
    readers = {name for name, c in _containers().items() if _secret_of(c) == "yal-gateway-credentials"}
    assert readers == {"yal-model-gateway"}


def test_each_service_reads_a_secret_with_what_it_needs() -> None:
    secrets, containers = _secrets(), _containers()
    supervisor = secrets[_secret_of(containers["yal-supervisor"])]
    assert {"DATABASE_URL", "SERVICE_JWT_SECRET", "VAULT_KEYS", "TEMPORAL_CODEC_KEY"} <= supervisor
    assert _secret_of(containers["yal-supervisor-worker"]) == _secret_of(containers["yal-supervisor"])
    memory = secrets[_secret_of(containers["yal-memory"])]
    assert memory == {"MEMORY_DATABASE_URL", "SERVICE_JWT_SECRET"}


def test_the_services_run_under_production_rules() -> None:
    for name in ("yal-supervisor", "yal-supervisor-worker", "yal-memory", "yal-model-gateway"):
        assert _env(_containers()[name]).get("ENV") == "production", name
