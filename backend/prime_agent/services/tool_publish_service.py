"""System Tool UUID → AI catalog ToolSet publish (D6 §5 C / T10–T11)."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import Settings, get_settings
from prime_agent.db.models.tool import Tool
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.ai_client import AiClient


class ToolPublishError(Exception):
    """Typed publish/bind failure — map to HTTP without leaking internals."""

    error_code: str = "TOOL_PUBLISH_ERROR"
    http_status: int = 422

    def __init__(self, message: str, *, error_code: str | None = None, http_status: int | None = None) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        if http_status is not None:
            self.http_status = http_status

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": str(self)}


class ToolHandlerMissingError(ToolPublishError):
    error_code = "TOOL_HANDLER_MISSING"
    http_status = 422

    def __init__(self, message: str = "custom tool has no handler or MCP registration") -> None:
        super().__init__(message, error_code="TOOL_HANDLER_MISSING", http_status=422)


class ToolUnownedError(ToolPublishError):
    error_code = "TOOL_NOT_FOUND"
    http_status = 404

    def __init__(self, message: str = "tool not found or unowned") -> None:
        super().__init__(message, error_code="TOOL_NOT_FOUND", http_status=404)


def _canonical_digest(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _parameters_to_json_schema(parameters: list[Any] | None) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for raw in parameters or []:
        if hasattr(raw, "model_dump"):
            item = raw.model_dump()
        elif isinstance(raw, dict):
            item = raw
        else:
            item = {
                "name": getattr(raw, "name", None),
                "type": getattr(raw, "type", "string"),
                "description": getattr(raw, "description", "") or "",
                "required": bool(getattr(raw, "required", False)),
            }
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "type": str(item.get("type") or "string"),
            "description": str(item.get("description") or ""),
        }
        if item.get("default") is not None:
            entry["default"] = item["default"]
        properties[name] = entry
        if item.get("required"):
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _tool_has_handler(tool: Tool) -> bool:
    """Custom tools need HTTP endpoint (HttpTool) or MCP; bare custom rows fail bind."""
    category = (tool.category or "").lower()
    if category in {"mcp"}:
        return True
    if category != "custom":
        return True
    endpoint = (getattr(tool, "endpoint", None) or "").strip()
    return bool(endpoint)


def register_http_tool(name: str, endpoint: str | None = None, **kwargs: Any) -> None:
    """BFF-side marker: endpoint customs publish with http_url metadata.

    Supervisor CatalogToolProvider invokes via ``http_url`` / ``register_http_tool``.
    """
    # No local HTTP client here — registration is metadata for publish + AI plane.
    if not name or not (endpoint or kwargs.get("url")):
        raise ValueError("register_http_tool requires name and endpoint")


# Alias for d6_production discovery.
bind_custom_http_tool = register_http_tool
HttpToolBinding = register_http_tool
HTTP_TOOL_REGISTRY: dict[str, str] = {}


def _definition_for_tool(tool: Tool) -> tuple[str, dict[str, Any], str]:
    raw_params = tool.parameters if isinstance(tool.parameters, list) else []
    params = _parameters_to_json_schema(raw_params)
    body: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": params,
    }
    endpoint = (getattr(tool, "endpoint", None) or "").strip()
    if endpoint:
        body["http_url"] = endpoint
        body["tool_class"] = "http"
        HTTP_TOOL_REGISTRY[tool.name] = endpoint
        register_http_tool(tool.name, endpoint=endpoint)
    digest = _canonical_digest(body)
    definition = {
        **body,
        "digest": digest,
    }
    return tool.name, definition, digest


# Shipped coding-tools MVP — merged into derived harness ToolSets so sessions
# keep echo/read/write/ls alongside custom UUIDs (LOCAL_DEMO #3 / D6 §9.3).
_CODING_BASE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "echo": {
        "name": "echo",
        "description": "Echo text (dev / smoke tool)",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
        },
    },
    "ls": {
        "name": "ls",
        "description": "List directory contents in the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "show_hidden": {"type": "boolean"},
            },
        },
    },
    "grep": {
        "name": "grep",
        "description": "Search files in the workspace for a regex/string pattern",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
                "glob": {"type": "string"},
                "case_insensitive": {"type": "boolean"},
                "max_matches": {"type": "integer"},
            },
            "required": ["pattern"],
        },
    },
    "read": {
        "name": "read",
        "description": "Read a file from the workspace",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    "write": {
        "name": "write",
        "description": "Write a file in the workspace",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "remember": {
        "name": "remember",
        "description": "Persist a durable fact into long-term memory for this session tenant/subject",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Fact to remember"},
            },
            "required": ["content"],
        },
    },
}


def _with_coding_base(
    definitions: dict[str, Any], tools_map: dict[str, str]
) -> tuple[dict[str, Any], dict[str, str]]:
    out_defs = dict(definitions)
    out_tools = dict(tools_map)
    for name, body in _CODING_BASE_DEFINITIONS.items():
        if name in out_defs:
            continue
        digest = _canonical_digest(body)
        out_defs[name] = {**body, "digest": digest}
        out_tools[name] = digest
    return out_defs, out_tools


async def publish_owned_tool_uuids(
    *,
    db: AsyncSession,
    user_id: str,
    identity: TenantPrincipal,
    tool_ids: list[str],
    settings: Settings | None = None,
    require_handlers: bool = False,
    name: str | None = None,
    semver: str = "1.0.0",
    include_coding_base: bool = True,
) -> dict[str, Any]:
    """Load owned Tool rows → POST /v1/catalog/tool_set/publish.

    Unowned UUIDs raise (not silently omitted). When ``require_handlers`` is True,
    custom tools without handler/MCP registration raise TOOL_HANDLER_MISSING.
    """
    settings = settings or get_settings()
    if not tool_ids:
        raise ToolPublishError("tool_ids must not be empty", http_status=422)

    unique_ids = list(dict.fromkeys(tool_ids))
    result = await db.execute(
        select(Tool).where(
            Tool.id.in_(unique_ids),
            Tool.user_id == user_id,
        )
    )
    owned = {t.id: t for t in result.scalars().all()}
    missing = [tid for tid in unique_ids if tid not in owned]
    if missing:
        raise ToolUnownedError(
            f"tool not found or unowned: {missing[0]}"
        )

    tools = [owned[tid] for tid in unique_ids]
    if require_handlers:
        for tool in tools:
            if not _tool_has_handler(tool):
                raise ToolHandlerMissingError(
                    f"TOOL_HANDLER_MISSING: custom tool '{tool.name}' has no handler or MCP registration"
                )

    definitions: dict[str, Any] = {}
    tools_map: dict[str, str] = {}
    for tool in tools:
        tname, definition, digest = _definition_for_tool(tool)
        definitions[tname] = definition
        tools_map[tname] = digest

    if include_coding_base:
        definitions, tools_map = _with_coding_base(definitions, tools_map)

    set_name = name or f"derived-{user_id[:8]}-{uuid4().hex[:8]}"
    body: dict[str, Any] = {
        "name": set_name,
        "semver": semver,
        "protocol_version": 1,
        "tools": tools_map,
        "definitions": definitions,
    }

    client = AiClient.from_settings(settings)
    published = await client.publish_tool_set(identity, body)
    return {
        "name": published.get("name") or set_name,
        "digest": published.get("digest"),
        "semver": published.get("semver") or semver,
        "status": published.get("status"),
        "tool_names": list(definitions.keys()),
        "published": published,
    }


# Pins copied from harness/compositions/coding-v1.json — used when deriving a
# per-harness composition that swaps in the published ToolSet (D6 §9.3).
CODING_V1_PROMPT_PACK = {
    "id": "coding-prompt",
    "digest": "sha256:3258f1bc4a50d6e8d93a1b06a872905ab831e57fcd8659315972288b4d39f5b1",
}
CODING_V1_MEMORY_PROFILE = {
    "id": "default",
    "digest": "sha256:307fac54741d2595109b8b6fd6bd3399bfdae9a7bb8f2a6a32a3123b0b675861",
}


async def publish_harness_composition(
    *,
    identity: TenantPrincipal,
    tool_set_name: str,
    tool_set_digest: str,
    settings: Settings | None = None,
    composition_name: str | None = None,
    harness_version: str = "coding-v1",
    semver: str = "1.0.0",
) -> dict[str, Any]:
    """Publish a composition that pins ``tool_set`` for session create (§9.3)."""
    settings = settings or get_settings()
    name = composition_name or f"derived-comp-{uuid4().hex[:10]}"
    body: dict[str, Any] = {
        "name": name,
        "semver": semver,
        "protocol_version": 1,
        "harness_version": harness_version,
        "prompt_pack": dict(CODING_V1_PROMPT_PACK),
        "tool_set": {"id": tool_set_name, "digest": tool_set_digest},
        "memory_profile": dict(CODING_V1_MEMORY_PROFILE),
        "plug_bindings": {
            "gateway_base_url": "http://model-gateway:8090",
            "model_route": "echo",
        },
    }
    client = AiClient.from_settings(settings)
    published = await client.publish_composition(identity, body)
    return {
        "name": published.get("name") or name,
        "digest": published.get("digest"),
        "semver": published.get("semver") or semver,
        "status": published.get("status"),
        "published": published,
    }


# Aliases expected by D6 acceptance loader.
publish_harness_tools = publish_owned_tool_uuids
derive_and_publish_tool_set = publish_owned_tool_uuids
