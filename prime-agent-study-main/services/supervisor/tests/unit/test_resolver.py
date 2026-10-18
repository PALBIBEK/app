"""HarnessResolver — exact version, cache, no latest."""

from __future__ import annotations

from pathlib import Path

import pytest

from supervisor.db.engine import open_db
from supervisor.harness import (
    HarnessCatalog,
    HarnessNotOffered,
    HarnessResolver,
    UnknownHarness,
    load_manifest,
)
from tests.support.session_env import make_catalog, make_manifest


@pytest.mark.asyncio
async def test_resolve_returns_manifest() -> None:
    catalog = make_catalog()
    r = HarnessResolver(catalog=catalog)
    m = await r.resolve("kg-v1")
    assert m.harness_version == "kg-v1"


@pytest.mark.asyncio
async def test_resolve_caches(tmp_path: Path) -> None:
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "kg-v1.yaml").write_text(
        """
harness_version: kg-v1
protocol_version: 1
runtime: echo
requires_sandbox: false
mode: stateless
recovery:
  default: fail
"""
    )
    r = HarnessResolver(versions_dir=versions)
    await r.resolve("kg-v1")
    assert r._io_calls == 1
    await r.resolve("kg-v1")
    assert r._io_calls == 1


@pytest.mark.asyncio
async def test_unknown_version_raises() -> None:
    r = HarnessResolver(catalog=make_catalog())
    with pytest.raises(UnknownHarness):
        await r.resolve("nope-v9")


@pytest.mark.asyncio
async def test_no_way_to_resolve_latest() -> None:
    r = HarnessResolver(catalog=make_catalog())
    with pytest.raises(UnknownHarness, match="latest"):
        await r.resolve("latest")
    with pytest.raises(UnknownHarness, match="latest"):
        await r.resolve("coding:latest")


@pytest.mark.asyncio
async def test_deprecated_refused_for_create_ok_for_resume(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        r = HarnessResolver(session_factory=factory)
        m = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
        await r.publish(m, status="active")
        await r.set_status("kg-v1", "deprecated")
        with pytest.raises(HarnessNotOffered):
            await r.resolve("kg-v1", for_create=True)
        got = await r.resolve("kg-v1", for_create=False)
        assert got.harness_version == "kg-v1"


def test_catalog_rejects_latest() -> None:
    c = make_catalog()
    with pytest.raises(UnknownHarness, match="latest"):
        c.resolve("latest")


def test_load_real_yaml_files() -> None:
    root = Path(__file__).resolve().parents[4] / "harness" / "versions"
    if not root.is_dir():
        root = Path(__file__).resolve().parents[5] / "harness" / "versions"
    coding = load_manifest((root / "coding-v1.yaml").read_text())
    assert coding.requires_sandbox is True
    kg = load_manifest((root / "kg-v1.yaml").read_text())
    assert kg.requires_sandbox is False
