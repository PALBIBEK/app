"""D6 §5 H / LOCAL_DEMO #3 — custom tool attach → session list_tools + invoke."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.catalog.digests import content_digest
from supervisor.catalog.providers.catalog_tools import CatalogToolProvider
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import (
    AgentComposition,
    DigestRef,
    ToolDefinition,
    ToolSet,
)
from supervisor.seams.types import ToolInvokeContext
from tests.unit.test_catalog_pins import _sample_prompt_pack


CUSTOM_NAME = "local_demo_custom_echo"


def _custom_tool_set() -> ToolSet:
    body = {
        "name": CUSTOM_NAME,
        "description": "LOCAL_DEMO custom tool",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
    }
    digest = content_digest(body)
    return ToolSet(
        name="d6-local-demo-tools",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        tools={CUSTOM_NAME: digest},
        definitions={
            CUSTOM_NAME: ToolDefinition(
                name=CUSTOM_NAME,
                description=body["description"],
                parameters=body["parameters"],
                digest=digest,
            )
        },
    )


@pytest.mark.asyncio
async def test_h_custom_tool_in_list_tools_and_invokable(session_env) -> None:
    """LOCAL_DEMO #3 / §5 H: attached custom tool name appears in list_tools; invoke works.

    Production: explicit ``register_handler`` (or http_url) — no silent echo fallback.
    """
    from supervisor.catalog.tool_handlers import register_handler

    def _demo(args, ctx):
        del ctx
        return {"text": str(args.get("text", ""))}

    register_handler(CUSTOM_NAME, _demo)
    cat = CompositionCatalog()
    pack = cat.offer(_sample_prompt_pack(name="d6-demo-pack"))
    tools = cat.offer(_custom_tool_set())
    comp = cat.offer(
        AgentComposition(
            name="d6-local-demo-comp",
            semver="1.0.0",
            digest="",
            protocol_version=1,
            status="offered",
            harness_version="coding-v1",
            prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
        )
    )
    m = session_env["manager"]
    m._composition_catalog = cat
    with patch("supervisor.session.manager.create_runtime", side_effect=create_runtime):
        sid = await m.create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp.name,
        )

    seams = m._seams_for_session(sid, await m._require_active(sid))
    assert seams is not None
    names = {t.name for t in seams.tool_provider.list_tools()}
    assert CUSTOM_NAME in names, f"session list_tools missing custom tool; got {names}"

    provider_result = seams.tool_provider.invoke(
        ToolInvokeContext(extra={"session_id": sid}),
        CUSTOM_NAME,
        {"text": "demo"},
    )
    assert provider_result.code != "TOOL_NOT_IMPLEMENTED", provider_result

    outcome = await m.invoke_tool(
        sid,
        tenant_id="t1",
        name=CUSTOM_NAME,
        arguments={"text": "demo"},
        tool_call_id="tc-demo-custom",
    )
    assert outcome.result.code != "TOOL_NOT_IMPLEMENTED", outcome.result
    assert outcome.result.ok is True, outcome.result
    await m.terminate(sid)


def test_h_builtin_handlers_or_registration_cover_custom_names() -> None:
    """Custom ToolSet names need register_handler or http_url — not echo fallback."""
    from supervisor.catalog import tool_handlers

    orphan = "d6_orphan_unregistered_tool"
    body = {
        "name": orphan,
        "description": "no handler",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
    }
    digest = content_digest(body)
    ts = ToolSet(
        name="d6-orphan-tools",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        tools={orphan: digest},
        definitions={
            orphan: ToolDefinition(
                name=orphan,
                description=body["description"],
                parameters=body["parameters"],
                digest=digest,
            )
        },
    )
    provider = CatalogToolProvider(ts)
    result = provider.invoke(ToolInvokeContext(), orphan, {"text": "x"})
    assert result.code == "TOOL_NOT_IMPLEMENTED", (
        "production: unregistered custom must not silently echo"
    )
    register = getattr(tool_handlers, "register_handler", None)
    assert callable(register), "need register_handler for custom tools"
    register(orphan, lambda args, ctx: {"text": args.get("text", "")})
    result2 = provider.invoke(ToolInvokeContext(), orphan, {"text": "x"})
    assert result2.ok is True
    assert result2.code != "TOOL_NOT_IMPLEMENTED"
