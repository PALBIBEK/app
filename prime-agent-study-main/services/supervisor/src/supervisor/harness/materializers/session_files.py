"""Materialize catalog-pinned seams into session_dir/.yal/ for harness extensions.

Creates the .yal/ directory structure with all necessary configuration
files for harness subprocess execution.

Reference: Original bind_files.py refactored with settings and security.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from supervisor.harness.config import HarnessSettings, get_settings
from supervisor.harness.core.types import SessionFilesPaths
from supervisor.harness.storage.validators import validate_session_dir

if TYPE_CHECKING:
    from supervisor.agent.seams import RuntimeSeams
    from supervisor.catalog.core.types import MemoryProfile, SandboxProfile, SessionPins

__all__ = [
    "materialize_harness_binds",
]

logger = logging.getLogger(__name__)


def _subagent_provider_meta(seams: RuntimeSeams) -> tuple[list[str], str]:
    """Extract subagent provider metadata from seams.

    Args:
        seams: Runtime seams

    Returns:
        Tuple of (provider names, default provider name)
    """
    transport = seams.subagent_transport
    providers: list[str] = []

    if transport is not None:
        registry = getattr(transport, "_registry", None)
        if registry is not None and hasattr(registry, "names"):
            providers = list(registry.names())

    default = "acp" if "acp" in providers else (providers[0] if providers else "memory")
    return providers, default


def _write_json(path: Path, data: dict) -> None:
    """Write JSON file with consistent formatting.

    Args:
        path: Path to write
        data: Dict to serialize
    """
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def materialize_harness_binds(
    session_dir: Path | str,
    pins: SessionPins,
    seams: RuntimeSeams,
    *,
    cwd: str | None = None,
    sandbox_profile: SandboxProfile | None = None,
    memory_profile: MemoryProfile | None = None,
    session_id: str | None = None,
    tenant_id: str | None = None,
    settings: HarnessSettings | None = None,
    memory_scope: Mapping[str, str] | None = None,
) -> SessionFilesPaths:
    """Write .yal/{pins.json,system_prompt.txt,tools.json,...} under session_dir.

    Host-plane only: digests in pins.json; prompt/tools from Protocol consumers.

    Args:
        session_dir: Root session directory
        pins: Session artifact pins
        seams: Runtime seams for prompt/tools assembly
        cwd: Working directory override
        sandbox_profile: Optional sandbox profile to materialize
        memory_profile: Optional memory profile to materialize
        session_id: Session identifier (defaults to directory name)
        tenant_id: Tenant identifier
        settings: Settings instance (uses get_settings() if None)

    Returns:
        SessionFilesPaths with paths to created files

    Raises:
        PathTraversalAttempt: If session_dir contains path traversal
    """
    from supervisor.seams import PromptBindings

    settings = settings or get_settings()
    base_url = settings.supervisor_base_url

    # Validate and create directories
    root = validate_session_dir(session_dir)
    yal = root / ".yal"
    yal.mkdir(parents=True, exist_ok=True)

    sid = session_id or root.name

    logger.debug(
        "Materializing harness binds",
        extra={"session_id": sid, "path": str(yal)},
    )

    # Track created files
    created_files: dict[str, str | None] = {
        "root": str(yal),
        "pins_json": None,
        "system_prompt_txt": None,
        "tools_json": None,
        "tool_invoke_json": None,
        "sandbox_profile_json": None,
        "memory_profile_json": None,
        "memory_rpc_json": None,
        "memory_service_json": None,
        "subagent_rpc_json": None,
        "plug_bindings_json": None,
        "launch_json": None,
    }

    # pins.json
    pins_path = yal / "pins.json"
    _write_json(pins_path, pins.to_dict())
    created_files["pins_json"] = str(pins_path)

    # sandbox_profile.json
    if sandbox_profile is not None:
        sp_path = yal / "sandbox_profile.json"
        _write_json(sp_path, {
            "id": sandbox_profile.name,
            "digest": sandbox_profile.digest,
            "provider": sandbox_profile.provider,
            "file_effects": sandbox_profile.file_effects,
            "approval_default": sandbox_profile.approval_default,
            "sticky_eligible": sandbox_profile.sticky_eligible,
            "image_digest": sandbox_profile.image_digest,
        })
        created_files["sandbox_profile_json"] = str(sp_path)

    # memory_profile.json and memory_rpc.json
    if memory_profile is not None:
        mp_path = yal / "memory_profile.json"
        mp_data = {
            "id": memory_profile.name,
            "digest": memory_profile.digest,
            "compaction": {
                "enabled": memory_profile.compaction_enabled,
                "reserve_tokens": memory_profile.reserve_tokens,
                "keep_recent_tokens": memory_profile.keep_recent_tokens,
                "pressure_ratio": memory_profile.pressure_ratio,
            },
            "recall": {
                "enabled": memory_profile.recall_enabled,
                "backend": memory_profile.recall_backend,
            },
        }
        if memory_profile.summarization_model:
            mp_data["compaction"]["summarization_model"] = memory_profile.summarization_model
        if memory_profile.recall_mcp_server_url:
            mp_data["recall"]["mcp_server_url"] = memory_profile.recall_mcp_server_url
        _write_json(mp_path, mp_data)
        created_files["memory_profile_json"] = str(mp_path)

        # memory_rpc.json
        mrpc_path = yal / "memory_rpc.json"
        memory_rpc_doc: dict[str, str] = {
            "compact_url": f"{base_url}/v1/sessions/{sid}/memory/compact",
        }
        if memory_profile.recall_enabled:
            memory_rpc_doc["recall_url"] = f"{base_url}/v1/sessions/{sid}/memory/recall"
        if tenant_id:
            memory_rpc_doc["tenant_id"] = tenant_id
        _write_json(mrpc_path, memory_rpc_doc)
        created_files["memory_rpc_json"] = str(mrpc_path)

    # Durable Memory Service bind (Tier 1)
    if settings.memory_service_url:
        ms_path = yal / "memory_service.json"
        durable_doc: dict[str, object] = {
            "base_url": settings.memory_service_url,
            "session_id": sid,
            # The supervisor's long-term scope (user and agent) when it gives one.
            "scope": dict(memory_scope)
            if memory_scope
            else {
                "tenant": tenant_id or settings.memory_tenant,
                "subject": settings.memory_subject or f"session:{sid}",
                "namespace": settings.memory_namespace,
            },
        }
        if settings.memory_lease_owner:
            durable_doc["lease_owner"] = settings.memory_lease_owner
        _write_json(ms_path, durable_doc)
        created_files["memory_service_json"] = str(ms_path)

    # system_prompt.txt
    work_cwd = cwd if cwd is not None else str(root.resolve())
    assembled = seams.prompt_assembler.assemble(PromptBindings(cwd=work_cwd))
    sp_txt_path = yal / "system_prompt.txt"
    sp_txt_path.write_text(assembled.text, encoding="utf-8")
    created_files["system_prompt_txt"] = str(sp_txt_path)

    # tools.json
    tools = [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in seams.tool_provider.list_tools()
    ]
    tools_path = yal / "tools.json"
    _write_json(tools_path, tools)
    created_files["tools_json"] = str(tools_path)

    # tool_invoke.json
    if tools:
        ti_path = yal / "tool_invoke.json"
        invoke_doc: dict[str, str] = {
            "url": f"{base_url}/v1/sessions/{sid}/tools/invoke",
        }
        if tenant_id:
            invoke_doc["tenant_id"] = tenant_id
        _write_json(ti_path, invoke_doc)
        created_files["tool_invoke_json"] = str(ti_path)

    # subagent_rpc.json
    providers, default_provider = _subagent_provider_meta(seams)
    if providers:
        sa_path = yal / "subagent_rpc.json"
        rpc_doc: dict[str, str | list[str]] = {
            "base_url": base_url,
            "session_id": sid,
            "spawn_path": f"/v1/sessions/{sid}/subagents/spawn",
            "fork_path": f"/v1/sessions/{sid}/subagents/fork",
            "list_children_path": f"/v1/sessions/{sid}/children",
            "providers": providers,
            "default_provider": default_provider,
        }
        if tenant_id:
            rpc_doc["tenant_id"] = tenant_id
        _write_json(sa_path, rpc_doc)
        created_files["subagent_rpc_json"] = str(sa_path)

    # plug_bindings.json
    plug_bindings = {
        k: v
        for k, v in {
            "gateway_base_url": pins.gateway_base_url,
            "mcp_server_url": pins.mcp_server_url,
            "acp_base_url": pins.acp_base_url,
            "model_route": pins.model_route,
        }.items()
        if v
    }
    if plug_bindings:
        pb_path = yal / "plug_bindings.json"
        _write_json(pb_path, plug_bindings)
        created_files["plug_bindings_json"] = str(pb_path)

    # launch.json
    if settings.harness_cli:
        lj_path = yal / "launch.json"
        _write_json(lj_path, {"cli": settings.harness_cli})
        created_files["launch_json"] = str(lj_path)

    logger.info(
        "Materialized harness binds",
        extra={
            "session_id": sid,
            "files_created": sum(1 for v in created_files.values() if v is not None),
        },
    )

    return SessionFilesPaths(
        root=created_files["root"],
        pins_json=created_files["pins_json"],
        system_prompt_txt=created_files["system_prompt_txt"],
        tools_json=created_files["tools_json"],
        tool_invoke_json=created_files["tool_invoke_json"],
        sandbox_profile_json=created_files["sandbox_profile_json"],
        memory_profile_json=created_files["memory_profile_json"],
        memory_rpc_json=created_files["memory_rpc_json"],
        memory_service_json=created_files["memory_service_json"],
        subagent_rpc_json=created_files["subagent_rpc_json"],
        plug_bindings_json=created_files["plug_bindings_json"],
        launch_json=created_files["launch_json"],
    )
