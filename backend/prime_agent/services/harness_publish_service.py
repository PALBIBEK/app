"""Publish a harness's draft manifest: catalog artifacts, a harness version, a release.

Order matters: the leaves (prompt pack, tool set, sandbox and memory profiles) and the
harness version first, the composition that pins their digests last. Every artifact is
named after the harness, so names (which belong to the first tenant to publish them)
never collide. A draft identical to the latest release publishes nothing.

A refusal says which part of the manifest it is about (``section``), so the builder
can show it on that tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import get_settings
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.harness_release import HarnessDraft, HarnessRelease
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.harness_manifest import HarnessManifest, SandboxSpec, default_manifest, manifest_body
from prime_agent.services.ai_client import AiClient, AiUpstreamError
from prime_agent.services.environment_service import EnvironmentService
from prime_agent.services.tool_publish_service import _canonical_digest

BASE_HARNESS_VERSION = "githi-v1"
"""The shipped manifest a published harness version starts from (runtime, image, recovery)."""

GATEWAY_URL = "http://model-gateway:8090"
"""Where sessions reach the model gateway: the shipped compositions' binding."""

SECTION_OF_KIND = {
    "prompt_pack": "prompt",
    "tool_set": "tools",
    "sandbox_profile": "sandbox",
    "memory_profile": "memory",
    "harness_version": "policy",
    "composition": "model",
}


class PublishError(Exception):
    def __init__(self, section: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.section = section
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": "PUBLISH_REFUSED", "section": self.section, "message": str(self)}


@dataclass(frozen=True)
class Published:
    release: HarnessRelease
    created: bool


def model_route(model_id: str) -> str:
    """``gateway/<route>`` (how /v1/models lists gateway routes) → the route a composition pins."""
    return model_id.removeprefix("gateway/")


def artifact_prefix(harness_id: str) -> str:
    return f"h-{harness_id.replace('-', '')[:12]}"


async def load_draft(harness_id: str, db: AsyncSession) -> HarnessManifest:
    draft = await db.get(HarnessDraft, harness_id)
    if draft:
        return HarnessManifest.model_validate(draft.manifest)
    settings = get_settings()
    return default_manifest(SandboxSpec(
        provider=settings.harness_default_sandbox_provider,
        file_effects=settings.harness_default_file_effects,
    ))


async def save_draft(harness_id: str, manifest: HarnessManifest, db: AsyncSession) -> None:
    draft = await db.get(HarnessDraft, harness_id)
    body = manifest_body(manifest)
    if draft is None:
        db.add(HarnessDraft(harness_id=harness_id, manifest=body))
    else:
        draft.manifest = body
    await db.commit()


async def releases(harness_id: str, db: AsyncSession) -> list[HarnessRelease]:
    rows = await db.execute(
        select(HarnessRelease).where(HarnessRelease.harness_id == harness_id).order_by(HarnessRelease.version.desc())
    )
    return list(rows.scalars().all())


class HarnessPublisher:
    def __init__(self, client: AiClient) -> None:
        self._client = client

    async def publish(
        self, harness: Harness, *, user_id: str, identity: TenantPrincipal, db: AsyncSession
    ) -> Published:
        manifest = await EnvironmentService.resolve(await load_draft(harness.id, db), harness.user_id, db)
        history = await releases(harness.id, db)
        latest = history[0] if history else None
        body = manifest_body(manifest)
        if latest is not None and latest.manifest == body:
            self._bind(harness, latest, user_id)
            await db.commit()
            return Published(latest, created=False)

        version = (latest.version if latest else 0) + 1
        artifacts = await self._artifacts(harness, manifest, version, identity)
        release = HarnessRelease(
            harness_id=harness.id,
            version=version,
            manifest=body,
            composition_id=artifacts["composition_id"],
            composition_digest=artifacts["composition_digest"],
            harness_version=artifacts["harness_version"],
            runtime=artifacts["runtime"],
            created_by=user_id,
        )
        db.add(release)
        self._bind(harness, release, user_id)
        harness.model = manifest.model
        harness.mode = "sticky" if manifest.sandbox.provider != "none" else "stateless"
        harness.tools = []  # built-in tools live in the manifest; custom tool UUIDs are not runnable
        harness.published_tools = list(manifest.tools)
        await db.commit()
        await db.refresh(release)
        return Published(release, created=True)

    @staticmethod
    def _bind(harness: Harness, release: HarnessRelease, user_id: str) -> None:
        harness.bind_status = "bound"
        harness.composition_id = release.composition_id
        harness.composition_digest = release.composition_digest
        harness.harness_version = release.harness_version
        harness.runtime = release.runtime
        harness.bound_at = datetime.now(UTC)
        harness.bound_by = user_id

    async def _artifacts(
        self, harness: Harness, m: HarnessManifest, version: int, identity: TenantPrincipal
    ) -> dict[str, str]:
        prefix = artifact_prefix(harness.id)
        semver = f"1.{version}.0"
        base = await self._call("policy", self._client.get_harness_version(identity, BASE_HARNESS_VERSION))
        base_manifest: dict[str, Any] = base.get("manifest") or {}
        registry = {t["name"]: t for t in await self._call("tools", self._client.list_tools(identity))}
        if unknown := [t for t in m.tools if t not in registry]:
            raise PublishError("tools", f"the supervisor cannot run: {', '.join(unknown)}")

        prompt = await self._publish(identity, "prompt_pack", {
            "name": f"{prefix}-prompt",
            "semver": semver,
            "protocol_version": 1,
            "persona": m.prompt.persona,
            "sections": [
                {"id": s.id, "order": i + 1, "text": s.text, "complete": True}
                for i, s in enumerate(m.prompt.sections)
            ],
            "vars": ["cwd"] if any("{cwd}" in s.text for s in m.prompt.sections) else [],
        })

        definitions: dict[str, Any] = {}
        for name in m.tools:
            spec = registry[name]
            definition = {
                "name": name,
                "description": spec.get("description") or "",
                "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
                "tool_class": spec.get("tool_class"),
            }
            definitions[name] = {**definition, "digest": _canonical_digest(definition)}
        tools = await self._publish(identity, "tool_set", {
            "name": f"{prefix}-tools",
            "semver": semver,
            "protocol_version": 1,
            "tools": {n: d["digest"] for n, d in definitions.items()},
            "definitions": definitions,
        })

        with_sandbox = m.sandbox.provider != "none"
        sandbox = None
        if with_sandbox:
            sandbox_body: dict[str, Any] = {
                "name": f"{prefix}-sandbox",
                "semver": semver,
                "protocol_version": 1,
                "provider": m.sandbox.provider,
                "file_effects": m.sandbox.file_effects,
                "approval_default": m.sandbox.approval_default,
                **({"start": m.sandbox.start} if m.sandbox.start is not None else {}),
                **({"idle_stop_s": m.sandbox.idle_stop_s} if m.sandbox.idle_stop_s is not None else {}),
                "sticky_eligible": True,
            }
            if base_manifest.get("image_digest") and m.sandbox.provider in ("local_docker", "e2b"):
                sandbox_body["image_digest"] = base_manifest["image_digest"]
            sandbox = await self._publish(identity, "sandbox_profile", sandbox_body)

        memory = await self._publish(identity, "memory_profile", {
            "name": f"{prefix}-memory",
            "semver": semver,
            "protocol_version": 1,
            "compaction": {
                "enabled": m.memory.compaction,
                "reserve_tokens": 16_384,
                "keep_recent_tokens": 20_000,
                "pressure_ratio": 0.8,
            },
            "recall": {"enabled": False, "backend": "none"},
        })

        harness_version = f"{prefix}-r{version}"
        p = m.policy
        registered = await self._call("policy", self._client.register_harness_version(identity, {
            "harness_version": harness_version,
            "protocol_version": 1,
            "runtime": base_manifest.get("runtime", "githi"),
            "requires_sandbox": with_sandbox,
            "mode": "sticky" if with_sandbox else "stateless",
            **({"image_digest": base_manifest["image_digest"]} if with_sandbox and base_manifest.get("image_digest") else {}),
            "model": model_route(m.model),
            "tools": {n: {"class": registry[n].get("tool_class") or "non_atomic"} for n in m.tools},
            "recovery": {
                "default": (base_manifest.get("recovery") or {}).get("default", "fail"),
                "by_tool_class": (base_manifest.get("recovery") or {}).get("by_tool_class", {}),
            },
            "budget": {"max_cost_per_session": p.max_cost_per_session, "max_tokens_per_turn": p.max_tokens_per_turn},
            "guards": {"repetition": p.repetition, "oscillation": p.oscillation, "stall_iterations": p.stall_iterations},
            "approvals": {
                "timeout_s": p.approval_timeout_s,
                "on_timeout": p.on_approval_timeout,
                "require_approval": list(p.require_approval),
            },
            "loop": {"max_steps": p.max_steps, "warn_steps_remaining": min(3, p.max_steps - 1), "force_answer_on_final_step": True},
            **({"subagents": base_manifest["subagents"]} if base_manifest.get("subagents") else {}),
        }))
        runtime = str(registered.get("runtime") or base_manifest.get("runtime") or "githi")

        composition: dict[str, Any] = {
            "name": prefix,
            "semver": semver,
            "protocol_version": 1,
            "harness_version": harness_version,
            "prompt_pack": prompt,
            "tool_set": tools,
            "memory_profile": memory,
            "plug_bindings": {"gateway_base_url": GATEWAY_URL, "model_route": model_route(m.model)},
        }
        if sandbox is not None:
            composition["sandbox_profile"] = sandbox
        published = await self._publish(identity, "composition", composition)
        return {
            "composition_id": published["id"],
            "composition_digest": published["digest"],
            "harness_version": harness_version,
            "runtime": runtime,
        }

    async def _publish(self, identity: TenantPrincipal, kind: str, body: dict[str, Any]) -> dict[str, str]:
        out = await self._call(SECTION_OF_KIND[kind], self._client.publish_artifact(identity, kind, body))
        artifact_id = out.get("artifact_id") or body["name"]
        return {"id": str(artifact_id), "digest": str(out["digest"])}

    @staticmethod
    async def _call(section: str, coro: Any) -> Any:
        try:
            return await coro
        except AiUpstreamError as exc:
            code = exc.status_code or 503
            if code >= 500:
                raise PublishError(section, "the supervisor is unavailable; nothing was published", status_code=503) from exc
            raise PublishError(section, str(exc), status_code=422) from exc
