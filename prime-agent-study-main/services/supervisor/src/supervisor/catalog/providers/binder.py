"""Bind SessionPins → RuntimeSeams via catalog-backed providers (sole concrete map)."""

from __future__ import annotations

from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog.providers.memory_resolve import load_pinned_memory_profile
from supervisor.catalog.providers.catalog_prompt import CatalogPromptAssembler
from supervisor.catalog.providers.catalog_tools import bind_tool_set
from supervisor.catalog.storage import CompositionCatalog
from supervisor.catalog.core.types import AgentComposition, PromptPack, SessionPins, ToolSet
from supervisor.seams import build_runtime_seams
from supervisor.seams.core.factory import build_context_memory_from_profile


def bind_runtime_seams(
    pins: SessionPins,
    catalog: CompositionCatalog,
) -> RuntimeSeams:
    """Materialize catalog + plug bindings from pinned digests (single bind path)."""
    pack = catalog.get(
        "prompt_pack",
        pins.prompt_pack_id,
        pins.prompt_pack_digest,
    )
    tools = catalog.get(
        "tool_set",
        pins.tool_set_id,
        pins.tool_set_digest,
    )
    assert isinstance(pack, PromptPack)
    assert isinstance(tools, ToolSet)

    comp = catalog.get("composition", pins.composition_id, pins.composition_digest)
    assert isinstance(comp, AgentComposition)
    plug = comp.plug_bindings

    gateway_url = pins.gateway_base_url or (plug.gateway_base_url if plug else None)
    mcp_url = pins.mcp_server_url or (plug.mcp_server_url if plug else None)
    acp_url = pins.acp_base_url or (plug.acp_base_url if plug else None)

    # Build gateway first so MemoryProfile can share the same client instance.
    seams = build_runtime_seams(
        tool_provider=bind_tool_set(tools),
        prompt_assembler=CatalogPromptAssembler(pack),
        gateway_base_url=gateway_url,
        mcp_server_url=mcp_url,
        acp_base_url=acp_url,
        register_acp=bool(acp_url),
    )
    profile = load_pinned_memory_profile(pins, catalog)
    if profile is None:
        return seams

    context_memory = build_context_memory_from_profile(profile, seams.model_gateway)
    return build_runtime_seams(
        tool_provider=seams.tool_provider,
        prompt_assembler=seams.prompt_assembler,
        subagent_transport=seams.subagent_transport,
        model_gateway=seams.model_gateway,
        context_memory=context_memory,
        register_memory=False,
        register_acp=False,
    )
