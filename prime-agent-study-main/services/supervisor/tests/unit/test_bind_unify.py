"""Phase 2 step 2 — unified bind path with plug bindings."""

from __future__ import annotations

import json
from pathlib import Path

from supervisor.catalog.binder import bind_runtime_seams
from supervisor.catalog.resolve import resolve_composition
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, PlugBindings
from supervisor.harness import materialize_harness_binds
from supervisor.seams.providers.composite_tools import CompositeToolProvider
from supervisor.seams.providers.http_gateway import HttpModelGatewayClient
from supervisor.seams.providers.mcp_tools import McpToolProvider
from tests.unit.test_catalog_pins import _sample_prompt_pack, _sample_tool_set


def _offer_composition_with_plugs(cat: CompositionCatalog) -> AgentComposition:
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    comp = AgentComposition(
        name="coding-plugged",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        harness_version="coding-v1",
        prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
        tool_set=DigestRef(id=tools.name, digest=tools.digest),
        plug_bindings=PlugBindings(
            gateway_base_url="http://gateway.test:8081",
            mcp_server_url="http://mcp.test:9000",
            acp_base_url="http://acp.test:8091",
            model_route="anthropic/claude-sonnet-4",
        ),
    )
    return cat.offer(comp)


def test_bind_runtime_seams_wires_plug_bindings_from_composition() -> None:
    cat = CompositionCatalog()
    comp = _offer_composition_with_plugs(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)

    assert isinstance(seams.model_gateway, HttpModelGatewayClient)
    assert seams.model_gateway._base == "http://gateway.test:8081"  # noqa: SLF001
    providers = seams.tool_provider._providers  # noqa: SLF001
    assert any(isinstance(p, McpToolProvider) for p in providers)
    assert "acp" in seams.subagent_transport._registry.names()  # noqa: SLF001


def test_materialize_writes_plug_bindings_json(tmp_path: Path) -> None:
    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    comp = AgentComposition(
        name="coding-bind-file",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        harness_version="coding-v1",
        prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
        tool_set=DigestRef(id=tools.name, digest=tools.digest),
        plug_bindings=PlugBindings(
            gateway_base_url="http://gateway.test:8081",
            model_route="anthropic/claude-sonnet-4",
        ),
    )
    comp = cat.offer(comp)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    yal = materialize_harness_binds(tmp_path, pins, seams)
    raw = json.loads((yal / "plug_bindings.json").read_text(encoding="utf-8"))
    assert raw["gateway_base_url"] == "http://gateway.test:8081"
    assert raw["model_route"] == "anthropic/claude-sonnet-4"
