"""Every tool set the platform ships can be served by the registry.

Pinned tool sets predate the registry and are immutable (their digests are
pinned by compositions and live sessions), so the registry has to accept what
they publish, not the other way round.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from supervisor.catalog.loading.bootstrap import default_catalog_dir
from supervisor.catalog.storage import tool_set_from_dict
from supervisor.tools import default_registry

TOOL_SETS = sorted((default_catalog_dir() / "tool_sets").glob("*.json"))


def _definitions(path: Path) -> list[tuple[str, dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(name, body) for name, body in sorted(data["definitions"].items())]


PINNED = [
    pytest.param(name, body, id=f"{path.stem}:{name}")
    for path in TOOL_SETS
    for name, body in _definitions(path)
]


def test_shipped_tool_sets_exist() -> None:
    assert {p.stem for p in TOOL_SETS} >= {"coding-tools", "githi-tools"}


@pytest.mark.parametrize(("name", "body"), PINNED)
def test_pinned_tool_is_implemented(name: str, body: dict[str, Any]) -> None:
    assert name in default_registry()


@pytest.mark.parametrize(("name", "body"), PINNED)
def test_every_pinned_argument_is_accepted(name: str, body: dict[str, Any]) -> None:
    """A model following the pinned schema must never hit BAD_ARGS for a name it was given."""
    spec = default_registry()[name]
    properties = body["parameters"].get("properties", {})
    sample = {prop: _sample(prop, schema, i) for i, (prop, schema) in enumerate(properties.items())}
    parsed = spec.parse(sample).model_dump()
    for prop, value in sample.items():
        assert value in parsed.values(), f"{name}: pinned argument {prop!r} was dropped"


@pytest.mark.parametrize(("name", "body"), PINNED)
def test_pinned_tool_class_agrees_with_the_implementation(name: str, body: dict[str, Any]) -> None:
    if body.get("tool_class") is not None:
        assert body["tool_class"] == default_registry()[name].tool_class.value


def test_registry_definitions_publish_as_a_tool_set() -> None:
    """The registry's own definitions are valid catalog content (round-trip through the parser)."""
    definitions = {spec.name: spec.definition() for spec in default_registry().values()}
    tool_set = tool_set_from_dict(
        {"name": "all-builtins", "semver": "1.0.0", "definitions": definitions}
    )
    assert set(tool_set.tools) == set(default_registry())


def _sample(prop: str, schema: dict[str, Any], index: int) -> Any:
    """A value unique to ``prop`` (booleans differ from every default, which is false)."""
    if schema["type"] == "array":  # one item, each of its own fields sampled
        item = schema["items"]
        return [{k: _sample(k, v, i) for i, (k, v) in enumerate(item.get("properties", {}).items())}]
    return {"string": f"value-of-{prop}", "integer": 10 + index, "boolean": True}[schema["type"]]
