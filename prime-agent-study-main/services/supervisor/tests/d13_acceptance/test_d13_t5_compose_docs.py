"""D13 §8 T5 — local/staging compose + docs include Jaeger + OTEL endpoint for demo."""

from __future__ import annotations

from pathlib import Path

from tests.d13_acceptance.conftest import AI_ROOT

ROOT_COMPOSE = AI_ROOT / "docker-compose.yml"
STAGING_COMPOSE = AI_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"

# E2E-only compose under supervisor/tests must NOT satisfy the local-demo gate alone.
E2E_ONLY = AI_ROOT / "services" / "supervisor" / "tests" / "e2e" / "docker-compose.yml"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required file: {path}"
    return path.read_text(encoding="utf-8")


def test_t5_root_or_staging_compose_includes_jaeger_and_otlp_endpoint() -> None:
    """T5: Local demo compose must ship Jaeger (and/or otel-collector) + OTEL env for supervisor.

    Supervisor e2e-only compose does not count as the product local-demo slice (D13 §5 A / LOCAL_DEMO #7).
    """
    root = _read(ROOT_COMPOSE)
    staging = _read(STAGING_COMPOSE) if STAGING_COMPOSE.is_file() else ""
    combined_product = root + "\n" + staging

    has_jaeger = "jaeger" in combined_product.lower()
    has_collector = "otel-collector" in combined_product.lower() or (
        "opentelemetry-collector" in combined_product.lower()
    )
    assert has_jaeger or has_collector, (
        "root and/or staging docker-compose must include jaeger and/or otel-collector "
        "for D13 local demo (e2e-only compose is insufficient)"
    )

    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in combined_product, (
        "root and/or staging compose must set OTEL_EXPORTER_OTLP_ENDPOINT on supervisor "
        "(pointing at collector/Jaeger) for local demo"
    )

    # Guard: claiming demo via e2e-only file while product compose lacks OTEL is FAIL.
    if E2E_ONLY.is_file():
        e2e = _read(E2E_ONLY)
        if "jaeger" in e2e.lower() and "OTEL_EXPORTER_OTLP_ENDPOINT" not in combined_product:
            raise AssertionError(
                "Jaeger exists only under tests/e2e — wire it into root/staging compose"
            )


