"""A session with a memory service gets its `memory_service.json` bind (CI: ai-auth-tests)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.catalog import SessionPins
from supervisor.harness import materialize_harness_binds
from supervisor.harness.config.settings import clear_settings_cache
from supervisor.seams.core.factory import build_runtime_seams


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Harness settings are read once per process; each test sets its own environment."""
    clear_settings_cache()
    yield
    clear_settings_cache()


def _pins() -> SessionPins:
    return SessionPins(
        composition_id="c",
        composition_digest="sha256:c",
        harness_version="coding-v1",
        prompt_pack_id="p",
        prompt_pack_digest="sha256:p",
        tool_set_id="t",
        tool_set_digest="sha256:t",
    )


def test_materialize_writes_memory_service_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://memory.test:8090")
    monkeypatch.setenv("MEMORY_SUBJECT", "repo:yal")
    monkeypatch.setenv("MEMORY_NAMESPACE", "facts")
    monkeypatch.delenv("MEMORY_LEASE_OWNER", raising=False)
    paths = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="sess-dm",
        tenant_id="tenant-a",
    )
    assert paths.memory_service_json is not None
    path = Path(paths.memory_service_json)
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["base_url"] == "http://memory.test:8090"
    assert raw["session_id"] == "sess-dm"
    assert raw["scope"] == {
        "tenant": "tenant-a",
        "subject": "repo:yal",
        "namespace": "facts",
    }
    assert "lease_owner" not in raw


def test_materialize_skips_memory_service_without_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_SERVICE_URL", raising=False)
    paths = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="sess-x",
        tenant_id="t",
    )
    assert paths.memory_service_json is None


def test_materialize_memory_service_lease_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("MEMORY_LEASE_OWNER", "pod-a")
    paths = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="s1",
        tenant_id="t1",
    )
    assert paths.memory_service_json is not None
    raw = json.loads(Path(paths.memory_service_json).read_text(encoding="utf-8"))
    assert raw["lease_owner"] == "pod-a"
