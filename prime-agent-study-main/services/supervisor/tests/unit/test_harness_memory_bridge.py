"""harness-compaction-bridge — memory_profile.json + Pi compaction hooks (R/G/R)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from supervisor.catalog.types import MemoryProfile, SessionPins


def _sample_memory_profile() -> MemoryProfile:
    return MemoryProfile(
        name="default",
        semver="1.0.0",
        digest="sha256:" + "d" * 64,
        protocol_version=1,
        status="offered",
        compaction_enabled=True,
        reserve_tokens=16_384,
        keep_recent_tokens=20_000,
        pressure_ratio=0.8,
        summarization_model="cheap-summary",
        recall_enabled=False,
        recall_backend="none",
    )


def test_hmb_u1_materialize_writes_memory_profile_json(tmp_path: Path) -> None:
    from supervisor.catalog.types import SessionPins
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    profile = _sample_memory_profile()
    pins = SessionPins(
        composition_id="coding-v1",
        composition_digest="sha256:" + "a" * 64,
        harness_version="coding-v1",
        prompt_pack_id="coding-prompt",
        prompt_pack_digest="sha256:" + "b" * 64,
        tool_set_id="coding-tools",
        tool_set_digest="sha256:" + "c" * 64,
        memory_profile_id=profile.name,
        memory_profile_digest=profile.digest,
    )
    seams = build_runtime_seams()
    yal = materialize_harness_binds(
        tmp_path,
        pins,
        seams,
        memory_profile=profile,
        session_id="sess-mem",
        tenant_id="tenant-1",
    )
    path = yal / "memory_profile.json"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["id"] == "default"
    assert raw["digest"] == profile.digest
    assert raw["compaction"]["enabled"] is True
    assert raw["compaction"]["summarization_model"] == "cheap-summary"
    assert raw["recall"]["backend"] == "none"


def test_hmb_u2_no_memory_profile_without_pin(tmp_path: Path) -> None:
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    pins = SessionPins(
        composition_id="x",
        composition_digest="sha256:" + "a" * 64,
        harness_version="kg-v1",
        prompt_pack_id="p",
        prompt_pack_digest="sha256:" + "b" * 64,
        tool_set_id="t",
        tool_set_digest="sha256:" + "c" * 64,
    )
    yal = materialize_harness_binds(tmp_path, pins, build_runtime_seams())
    assert not (yal / "memory_profile.json").exists()


def test_hmb_c1_memory_profile_fixture_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    fixture = root / "harness" / "memory_profiles" / "default.json"
    assert fixture.is_file()
    data = json.loads(fixture.read_text(encoding="utf-8"))
    assert data["name"] == "default"
    assert data["compaction"]["enabled"] is True


def test_hmb_c2_harness_extension_module_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    assert (root / "harness" / "src" / "extensions" / "memory" / "index.ts").is_file()


def test_hmb_n1_node_memory_extension_loads_profile(tmp_path: Path, monkeypatch) -> None:
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams

    profile = _sample_memory_profile()
    pins = SessionPins(
        composition_id="coding-v1",
        composition_digest="sha256:" + "a" * 64,
        harness_version="coding-v1",
        prompt_pack_id="p",
        prompt_pack_digest="sha256:" + "b" * 64,
        tool_set_id="t",
        tool_set_digest="sha256:" + "c" * 64,
    )
    materialize_harness_binds(
        tmp_path,
        pins,
        build_runtime_seams(gateway_base_url="http://gateway.test:8081"),
        memory_profile=profile,
        session_id="sess-1",
    )
    harness_root = Path(__file__).resolve().parents[4] / "harness"
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node is None or npm is None:
        pytest.skip("node/npm not installed")
    build = subprocess.run(
        [npm, "run", "build"],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, build.stderr or build.stdout
    check = subprocess.run(
        [node, "scripts/check-memory.mjs", str(tmp_path)],
        cwd=harness_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stderr or check.stdout
    payload = json.loads(check.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["compaction_enabled"] is True
    assert payload["summarization_model"] == "cheap-summary"
