"""DA-B* — durable memory_service.json bind materialization."""

from __future__ import annotations

import json
from pathlib import Path

from supervisor.catalog.types import SessionPins
from supervisor.harness import materialize_harness_binds
from supervisor.seams.factory import build_runtime_seams


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
    yal = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="sess-dm",
        tenant_id="tenant-a",
    )
    path = yal / "memory_service.json"
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
    yal = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="sess-x",
        tenant_id="t",
    )
    assert not (yal / "memory_service.json").exists()


def test_materialize_memory_service_lease_owner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_SERVICE_URL", "http://127.0.0.1:8090")
    monkeypatch.setenv("MEMORY_LEASE_OWNER", "pod-a")
    yal = materialize_harness_binds(
        tmp_path,
        _pins(),
        build_runtime_seams(),
        session_id="s1",
        tenant_id="t1",
    )
    raw = json.loads((yal / "memory_service.json").read_text(encoding="utf-8"))
    assert raw["lease_owner"] == "pod-a"
