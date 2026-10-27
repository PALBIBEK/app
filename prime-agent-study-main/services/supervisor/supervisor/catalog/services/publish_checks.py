"""What publish refuses: configurations that parse but can never work as written.

The catalog's parsers are lenient (unknown keys are dropped), so a typo, or a
setting put at the wrong level, used to be accepted and silently ignored: a
memory profile with ``summarization_model`` at the top level instead of under
``compaction`` kept the default, and nobody was told. Publish now refuses:

- any key the artifact does not have, at any depth (F28);
- a memory profile that recalls over MCP without naming the server (F11);
- a model the gateway does not serve: a composition's ``model_route``, a memory
  profile's summarizer (F17). Checked only when the gateway's models are known,
  so a gateway outage does not block publishing;
- a composition's named hand whose name a tool call could not carry (lowercase
  letters, digits, ``-`` and ``_``, at most 32), or ``default``, which is the
  sandbox profile's own name, or a named hand on a composition with no default;
- an artifact schema (``protocol_version``) this supervisor does not run;
- a prompt pack with a section marked unfinished (``complete: false``): it would
  reach the model as written.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.core.types import (
    SUPPORTED_PROTOCOL_VERSIONS,
    MemoryProfile,
    PromptPack,
    RecallBackend,
)
from supervisor.catalog.storage import artifact_to_dict
from supervisor.hands.keys import DEFAULT_HAND

__all__ = ["check_publishable"]

_IGNORED = frozenset({"digest", "metadata"})  # derived (recomputed) or assigned by the catalog
_HAND_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def check_publishable(kind: str, body: Mapping[str, Any], artifact: Any) -> None:
    """Raise ``InvalidArtifact`` (422) if ``body`` cannot work as the ``artifact`` it parsed into."""
    name = str(body.get("name", "?"))
    unknown = sorted(_unknown_keys(body, artifact_to_dict(artifact)))
    if unknown:
        raise InvalidArtifact(kind, name, f"unknown field(s): {', '.join(unknown)}")
    if artifact.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise InvalidArtifact(
            kind,
            name,
            f"protocol_version {artifact.protocol_version}: this supervisor runs "
            f"{', '.join(map(str, sorted(SUPPORTED_PROTOCOL_VERSIONS)))}",
        )
    if isinstance(artifact, PromptPack):
        _check_prompt_pack(name, artifact)
    if kind == "memory_profile":
        _check_memory_profile(name, artifact)
    if kind == "composition":
        _check_hands(name, artifact)
    if kind == "sandbox_profile":
        _check_runner(name, artifact)
        _check_lifecycle(name, artifact)
    for field, model in _models_named(kind, artifact):
        _check_served(kind, name, field, model)


SANDBOX_STARTS = ("with_run", "on_first_tool")
"""When a sandbox is built (``SandboxProfile.start``)."""


def _unknown_keys(given: Mapping[str, Any], parsed: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in given.items():
        if key in _IGNORED and not prefix:
            continue
        path = f"{prefix}{key}"
        if key not in parsed:
            yield path
        elif isinstance(value, Mapping) and isinstance(parsed[key], Mapping):
            yield from _unknown_keys(value, parsed[key], f"{path}.")


def _check_runner(name: str, profile: Any) -> None:
    on_runner = str(getattr(profile.provider, "value", profile.provider)) == "runner"
    if on_runner and not profile.runner:
        raise InvalidArtifact("sandbox_profile", name, "provider 'runner' needs runner: the runner's name")
    if profile.runner and not on_runner:
        raise InvalidArtifact("sandbox_profile", name, "runner is only for provider 'runner'")


def _check_lifecycle(name: str, profile: Any) -> None:
    if profile.start is not None and profile.start not in SANDBOX_STARTS:
        raise InvalidArtifact(
            "sandbox_profile", name, f"start {profile.start!r}: use one of {', '.join(SANDBOX_STARTS)}"
        )
    if profile.idle_stop_s is not None and not 0 <= profile.idle_stop_s <= 86_400:
        raise InvalidArtifact("sandbox_profile", name, "idle_stop_s must be 0 (never) to 86400 seconds")


def _check_hands(name: str, composition: Any) -> None:
    for hand, _ in composition.hands:
        if hand == DEFAULT_HAND or not _HAND_NAME.fullmatch(hand):
            raise InvalidArtifact(
                "composition",
                name,
                f"hand name {hand!r}: use lowercase letters, digits, '-' or '_' (at most 32), "
                f"not {DEFAULT_HAND!r}",
            )
    if composition.hands and composition.sandbox_profile is None:
        raise InvalidArtifact("composition", name, "named hands need a sandbox_profile (the default hand)")


def _check_prompt_pack(name: str, pack: PromptPack) -> None:
    if unfinished := [s.id for s in pack.sections if not s.complete]:
        raise InvalidArtifact(
            "prompt_pack", name, f"section(s) marked unfinished: {', '.join(unfinished)}; finish or remove them"
        )


def _check_memory_profile(name: str, profile: MemoryProfile) -> None:
    if (
        profile.recall_enabled
        and profile.recall_backend == RecallBackend.MCP
        and not (profile.recall_mcp_server_url or "").strip()
    ):
        raise InvalidArtifact("memory_profile", name, "recall backend 'mcp' needs recall.mcp_server_url")


def _models_named(kind: str, artifact: Any) -> Iterator[tuple[str, str]]:
    if kind == "composition" and artifact.plug_bindings and artifact.plug_bindings.model_route:
        yield "plug_bindings.model_route", artifact.plug_bindings.model_route
    if kind == "memory_profile" and artifact.compaction_enabled and artifact.summarization_model:
        yield "compaction.summarization_model", artifact.summarization_model


def _check_served(kind: str, name: str, field: str, model: str) -> None:
    from supervisor.models import ModelUnavailable, model_router

    router = model_router()
    if router is None:
        return
    try:
        router.refresh()
    except Exception:  # noqa: BLE001 — an unreachable gateway is no reason to refuse a publish
        return
    if not router.catalog.admitted.chat_models(include_aliased=True):
        return  # the gateway's models are unknown right now
    try:
        router.resolve(model)
    except ModelUnavailable as exc:
        raise InvalidArtifact(kind, name, f"{field} {model!r} is not served by the model gateway") from exc
