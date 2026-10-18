"""D7 §5 A / deploy — Memory compose port ≠ gateway; MEMORY_SERVICE_URL bind."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.d7_acceptance.conftest import AI_REPO, GATEWAY_PORT


def _staging_compose() -> Path:
    return AI_REPO / "deploy" / "staging" / "docker-compose.staging.yml"


def test_a_staging_compose_includes_memory_service() -> None:
    """§5 A: Memory Service in staging compose with healthcheck."""
    path = _staging_compose()
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*memory:\s*$", text), "staging compose missing memory service"
    assert "readyz" in text
    assert "MEMORY_SERVICE_URL" in text
    assert "healthcheck" in text


def test_a_memory_port_does_not_collide_with_gateway() -> None:
    """§5 A / rule 11: Memory listen port must not equal gateway (8090)."""
    path = _staging_compose()
    text = path.read_text(encoding="utf-8")
    # Extract MEMORY_PORT from memory service block (best-effort).
    mem_block = re.search(
        r"(?ms)^  memory:\s*\n(.*?)(?=^  [a-z0-9_-]+:\s*$|\Z)",
        text,
    )
    assert mem_block, "could not parse memory service block"
    block = mem_block.group(1)
    ports = re.findall(r"MEMORY_PORT:\s*[\"']?(\d+)", block)
    published = re.findall(r"[\"'](\d+):\d+[\"']", block)
    candidates = {int(p) for p in ports + published}
    assert candidates, "memory service must declare MEMORY_PORT or published ports"
    assert GATEWAY_PORT not in candidates, (
        f"D7: Memory port collides with gateway {GATEWAY_PORT}: {candidates}"
    )


def test_a_default_memory_port_not_gateway() -> None:
    """Default MEMORY_PORT in __main__ must not be gateway 8090."""
    main = AI_REPO / "services" / "memory" / "src" / "memory_service" / "__main__.py"
    assert main.is_file(), main
    text = main.read_text(encoding="utf-8")
    # Prefer AST parse of default in os.environ.get("MEMORY_PORT", "NNNN")
    tree = ast.parse(text)
    defaults: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr != "get" or len(node.args) < 2:
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value == "MEMORY_PORT":
                d = node.args[1]
                if isinstance(d, ast.Constant):
                    defaults.append(str(d.value))
    assert defaults, "could not find MEMORY_PORT default in __main__.py"
    for d in defaults:
        assert int(d) != GATEWAY_PORT, f"default MEMORY_PORT={d} collides with gateway"


def test_a_memory_service_json_materialized_when_url_set() -> None:
    """§5 A: bind materializes memory_service.json when MEMORY_SERVICE_URL set."""
    from supervisor.catalog.types import SessionPins
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams
    import json
    from pathlib import Path as P
    import os

    pins = SessionPins(
        composition_id="c",
        composition_digest="sha256:c",
        harness_version="coding-v1",
        prompt_pack_id="p",
        prompt_pack_digest="sha256:p",
        tool_set_id="t",
        tool_set_digest="sha256:t",
    )
    # Use a unique tmp under AI_REPO-adjacent via pytest-less pathlib mkdtemp
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        os.environ["MEMORY_SERVICE_URL"] = "http://memory:8092"
        try:
            yal = materialize_harness_binds(
                P(td),
                pins,
                build_runtime_seams(),
                session_id="d7-bind",
                tenant_id="tenant-d7",
            )
            path = yal / "memory_service.json"
            assert path.is_file(), "MEMORY_SERVICE_URL set but memory_service.json missing"
            raw = json.loads(path.read_text(encoding="utf-8"))
            assert raw["base_url"] == "http://memory:8092"
        finally:
            os.environ.pop("MEMORY_SERVICE_URL", None)


def test_t11_supervisor_and_memory_outboxes_not_cross_wired() -> None:
    """T11 / ADR-004: supervisor tool_outbox ≠ Memory outbox; no cross imports."""
    tool_outbox = (
        AI_REPO / "services" / "supervisor" / "src" / "supervisor" / "session" / "tool_outbox.py"
    )
    mem_src = AI_REPO / "services" / "memory" / "src" / "memory_service"
    mem_outbox_hits = list(mem_src.rglob("*outbox*"))
    assert tool_outbox.is_file(), "supervisor tool_outbox missing"
    assert mem_outbox_hits, "Memory service must own its own outbox module/table"

    invoker = AI_REPO / "services" / "supervisor" / "src" / "supervisor" / "session" / "manager.py"
    text = invoker.read_text(encoding="utf-8")
    assert "memory_service.outbox" not in text
    assert "from memory_service" not in text

    mem_app = mem_src / "app.py"
    mem_text = mem_app.read_text(encoding="utf-8")
    assert "tool_outbox" not in mem_text
    assert "supervisor.session" not in mem_text


def test_durable_client_exposes_tier2_items_and_recall() -> None:
    """§5 C / gaps: Pi DurableMemoryClient must expose Tier 2 items + recall."""
    client = AI_REPO / "harness" / "src" / "extensions" / "memory" / "durable" / "client.ts"
    assert client.is_file(), client
    text = client.read_text(encoding="utf-8")
    has_items = (
        "/v1/memory/items" in text
        or "writeItem" in text
        or "write_item" in text
        or "createItem" in text
    )
    has_recall = (
        "/v1/memory/recall" in text
        or re.search(r"\basync\s+recall\s*\(", text) is not None
        or re.search(r"\brecallItem", text) is not None
    )
    assert has_items and has_recall, (
        "D7: DurableMemoryClient Tier 2 methods missing "
        "(need items write + recall against durable bank)"
    )
