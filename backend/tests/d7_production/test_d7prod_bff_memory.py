"""D7 FULL-PROD — outer BFF Memory list/search/forget (+ erase) routes."""

from __future__ import annotations

import re

from tests.d7_production.conftest import PRIME_AGENT, app_memory_route_paths, source_blob


REQUIRED_OPS = ("list", "search", "forget")
PREFERRED_OPS = ("erase", "erase_subject")


def test_prod_bff_exposes_memory_list_search_forget_routes() -> None:
    """Outer BFF must register /api/v1/memory list + search + forget (durable proxy)."""
    paths = app_memory_route_paths()
    assert paths, (
        "D7 FULL-PROD: no /api/v1/memory* routes on BFF — "
        "wire list/search/forget(+erase) proxy to Durable Memory"
    )

    joined = " ".join(sorted(paths)).lower()
    blob = source_blob(
        "web/routes/*.py",
        "web/routes/**/*.py",
        "services/*memory*.py",
        "services/ai_client.py",
    ).lower()

    def has_op(op: str) -> bool:
        patterns = (
            rf"/memory[^\n]*{op}",
            rf"/memory/{op}",
            rf"memory:{op}",
            rf"def\s+{op}_?memory",
            rf"async\s+def\s+{op}",
            rf'"{op}"',
            rf"/{op}",
        )
        return any(re.search(p, joined) or re.search(p, blob) for p in patterns)

    missing = [op for op in REQUIRED_OPS if not has_op(op)]
    assert not missing, (
        f"D7 FULL-PROD: BFF memory routes missing ops {missing}; "
        f"paths={sorted(paths)}"
    )


def test_prod_bff_preferably_exposes_erase_subject() -> None:
    """Prefer erase / erase_subject on BFF for GDPR-shaped product privacy."""
    paths = app_memory_route_paths()
    blob = source_blob(
        "web/routes/*.py",
        "web/routes/**/*.py",
        "services/*memory*.py",
        "services/ai_client.py",
    )
    combined = (" ".join(paths) + "\n" + blob).lower()
    has_erase = any(
        token in combined
        for token in ("erase_subject", "erase-subject", "/erase", "erase_subject")
    )
    assert has_erase, (
        "D7 FULL-PROD: BFF should expose erase_subject (or /erase) for product privacy"
    )


def test_prod_bff_proxies_durable_memory_not_local_fake() -> None:
    """If memory routes exist, they must call Durable Memory (/v1/memory or MEMORY_SERVICE)."""
    paths = app_memory_route_paths()
    if not paths:
        raise AssertionError("D7 FULL-PROD: no memory BFF routes to validate durable proxy")

    blob = source_blob(
        "web/routes/*.py",
        "web/routes/**/*.py",
        "services/*memory*.py",
        "services/ai_client.py",
        "services/**/*memory*.py",
    )
    durable = (
        "/v1/memory/" in blob
        or "MEMORY_SERVICE" in blob
        or "memory_service" in blob.lower()
        or "durable" in blob.lower()
    )
    assert durable, (
        "D7 FULL-PROD: memory BFF routes exist but do not proxy Durable Memory Service"
    )


def test_prod_ai_client_exposes_memory_methods() -> None:
    """AiClient (or dedicated memory client) must expose durable memory list/search/forget."""
    ai = (PRIME_AGENT / "services" / "ai_client.py").read_text(encoding="utf-8")
    extra = ""
    for path in sorted((PRIME_AGENT / "services").glob("*memory*")):
        if path.suffix == ".py":
            extra += path.read_text(encoding="utf-8")
    blob = ai + "\n" + extra

    # Require durable Memory HTTP paths or clearly named memory client methods.
    durable_hit = "/v1/memory/" in blob or "MEMORY_SERVICE" in blob
    named = all(
        re.search(
            rf"def\s+(memory_)?{name}(_memory|_items|_subject)?\b|"
            rf"async\s+def\s+(memory_)?{name}\b|"
            rf"/v1/memory/{name}|/v1/memory/.*{name}",
            blob,
            re.I,
        )
        for name in ("list", "search", "forget")
    )
    assert durable_hit and named, (
        "D7 FULL-PROD: AiClient/memory client must call /v1/memory/* and expose "
        "list/search/forget (got durable_hit="
        f"{durable_hit}, named={named})"
    )
