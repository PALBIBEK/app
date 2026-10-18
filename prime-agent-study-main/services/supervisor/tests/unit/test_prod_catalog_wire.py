"""Phase 2 step 1 — production composition catalog wire."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from supervisor.app import build_supervisor
from supervisor.bootstrap import bootstrap_supervisor
from supervisor.catalog.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.verify import verify_composition_catalog
from supervisor.config import Settings
from supervisor.db.engine import open_db
from supervisor.main import create_app
from tests.support.session_env import make_catalog


def _shipped_catalog_dir() -> Path:
    return default_catalog_dir()


def test_build_supervisor_wires_composition_catalog() -> None:
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    async def _run() -> None:
        async with open_db() as (_, factory):
            state = build_supervisor(
                session_factory=factory,
                catalog=make_catalog(),
                composition_catalog=cat,
            )
            assert state.composition_catalog is cat
            assert state.manager._composition_catalog is cat  # noqa: SLF001

    import asyncio

    asyncio.run(_run())


def test_shipped_catalog_fixture_parity() -> None:
    catalog_dir = _shipped_catalog_dir()
    catalog = bootstrap_composition_catalog(catalog_dir, fail_on_verify=False)
    problems = verify_composition_catalog(catalog, catalog_dir)
    assert problems == [], problems

    comp = catalog.get(
        "composition",
        "coding-v1",
        next(
            d
            for (kind, name, d), art in catalog._by_key.items()  # noqa: SLF001
            if kind == "composition" and name == "coding-v1" and art.status == "offered"
        ),
        require_offered=True,
    )
    assert comp.harness_version == "coding-v1"


def test_bootstrap_soft_skips_invalid_composition_keeps_coding(tmp_path: Path) -> None:
    """Invalid fixtures must not fail boot when coding-v1 (or any valid) remains offered."""
    import json
    import shutil

    src = _shipped_catalog_dir()
    dst = tmp_path / "harness"
    shutil.copytree(src, dst)

    broken = {
        "digest": "sha256:" + "f" * 64,
        "harness_version": "coding-v1",
        "name": "broken-tools-v1",
        "protocol_version": 1,
        "semver": "1.0.0",
        "status": "offered",
        "prompt_pack": {
            "id": "coding-prompt",
            "digest": "sha256:3258f1bc4a50d6e8d93a1b06a872905ab831e57fcd8659315972288b4d39f5b1",
        },
        # githi-tools lacks coding-v1 recovery tools bash/git_commit → verify fails
        "tool_set": {
            "id": "githi-tools",
            "digest": "sha256:d49abd6d6bbf6230ee084c01dd8cee6301f19aecbe1b64afe18d28ea9259b124",
        },
    }
    (dst / "compositions" / "broken-tools-v1.json").write_text(
        json.dumps(broken, indent=2) + "\n", encoding="utf-8"
    )

    catalog = bootstrap_composition_catalog(dst, fail_on_verify=True)
    offered = {
        a.name for a in catalog.list_artifacts("composition", status="offered")
    }
    assert "coding-v1" in offered
    assert "broken-tools-v1" not in offered


@pytest.mark.asyncio
async def test_api_create_session_with_composition_id(tmp_path) -> None:
    catalog_dir = _shipped_catalog_dir()
    composition_catalog = bootstrap_composition_catalog(catalog_dir)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        blob_dir=tmp_path / "blobs",
        load_composition_catalog=False,
        catalog_dir=catalog_dir,
    )
    state = await bootstrap_supervisor(settings, composition_catalog=composition_catalog)
    app = create_app(state=state)
    headers = {"X-Tenant-Id": "t1"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cr = await client.post(
            "/v1/sessions",
            json={
                "harness_version": "coding-v1",
                "runtime": "echo",
                "composition_id": "coding-v1",
            },
            headers=headers,
        )
        assert cr.status_code == 201, cr.text
        sid = cr.json()["session_id"]

        dr = await client.delete(f"/v1/sessions/{sid}", headers=headers)
        assert dr.status_code == 204
