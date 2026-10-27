"""A release that changes a shipped artifact keeps the version before it resolvable.

Found by shipping a changed tool set: shipped artifacts lived only in the release's
files, so the old version vanished, every tenant composition built on it broke, and
the API's readiness failed for everyone. Now shipped versions are recorded in the
database at boot, the versions this repo replaced are kept frozen in
``harness/previous/``, and readiness only considers what the deployment ships.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from supervisor.catalog.core.types import DigestRef
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.catalog.loading.verify import list_catalog_problems
from supervisor.catalog.repository import hydrate_composition_catalog, persist_shipped
from supervisor.catalog.storage import CompositionCatalog
from supervisor.db.engine import open_db

HARNESS = Path(__file__).resolve().parents[4] / "harness"


def test_the_shipped_catalog_keeps_the_versions_it_replaced() -> None:
    catalog = load_composition_catalog(HARNESS)
    for kind, name in (("composition", "githi-v1"), ("tool_set", "githi-tools")):
        versions = catalog.list_versions(kind, name)
        assert len(versions) >= 2, (name, versions)
        latest = catalog.get(kind, name, versions[-1])
        current = json.loads((HARNESS / f"{kind}s" / f"{name}.json").read_text())
        assert latest.semver == current["semver"], "the current version stays the latest"
    assert list_catalog_problems(catalog) == []


@pytest.mark.asyncio
async def test_a_shipped_version_a_later_release_drops_still_resolves(tmp_path: Path) -> None:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}") as (_, factory):
        release_n = bootstrap_composition_catalog(HARNESS)
        base = release_n.get("composition", "githi-v1", release_n.list_versions("composition", "githi-v1")[-1])
        only_in_n = release_n.offer(dataclasses.replace(base, name="shipped-in-n", digest=""))
        assert await persist_shipped(factory, release_n) > 0
        assert await persist_shipped(factory, release_n) == 0, "insert-only: a second boot adds nothing"

        release_n1 = bootstrap_composition_catalog(HARNESS)  # its files no longer ship it
        assert not release_n1.list_versions("composition", "shipped-in-n")
        await hydrate_composition_catalog(release_n1, factory)
        assert release_n1.get("composition", "shipped-in-n", only_in_n.digest).name == "shipped-in-n"


def test_a_tenants_broken_composition_does_not_fail_readiness() -> None:
    catalog: CompositionCatalog = load_composition_catalog(HARNESS)
    base = catalog.get("composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1])
    broken = dataclasses.replace(
        base, name="tenant-broken", digest="", tool_set=DigestRef(id="gone", digest="sha256:" + "0" * 64)
    )
    catalog.offer(broken, owner="t1")
    assert any("tenant-broken" in p for p in list_catalog_problems(catalog))
    assert list_catalog_problems(catalog, shipped_only=True) == []
