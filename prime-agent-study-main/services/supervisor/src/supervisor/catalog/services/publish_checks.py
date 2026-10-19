"""What publish refuses: configurations that parse but can never work as written.

The catalog's parsers are lenient (unknown keys are dropped), so a typo, or a
setting put at the wrong level, used to be accepted and silently ignored: a
memory profile with ``summarization_model`` at the top level instead of under
``compaction`` kept the default, and nobody was told. Publish now refuses:

- any key the artifact does not have, at any depth (F28);
- a memory profile that recalls over MCP without naming the server (F11);
- a model the gateway does not serve: a composition's ``model_route``, a memory
  profile's summarizer (F17). Checked only when the gateway's models are known,
  so a gateway outage does not block publishing.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.storage import artifact_to_dict

__all__ = ["check_publishable"]

_IGNORED = frozenset({"digest"})  # derived from the content, recomputed on publish


def check_publishable(kind: str, body: Mapping[str, Any], artifact: Any) -> None:
    """Raise ``InvalidArtifact`` (422) if ``body`` cannot work as the ``artifact`` it parsed into."""
    name = str(body.get("name", "?"))
    unknown = sorted(_unknown_keys(body, artifact_to_dict(artifact)))
    if unknown:
        raise InvalidArtifact(kind, name, f"unknown field(s): {', '.join(unknown)}")
    if kind == "memory_profile":
        _check_memory_profile(name, artifact)
    for field, model in _models_named(kind, artifact):
        _check_served(kind, name, field, model)


def _unknown_keys(given: Mapping[str, Any], parsed: Mapping[str, Any], prefix: str = "") -> Iterator[str]:
    for key, value in given.items():
        if key in _IGNORED and not prefix:
            continue
        path = f"{prefix}{key}"
        if key not in parsed:
            yield path
        elif isinstance(value, Mapping) and isinstance(parsed[key], Mapping):
            yield from _unknown_keys(value, parsed[key], f"{path}.")


def _check_memory_profile(name: str, profile: Any) -> None:
    if (
        profile.recall_enabled
        and profile.recall_backend == "mcp"
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
