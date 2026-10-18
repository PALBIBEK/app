"""End-to-end agent pipeline on the shipped catalog, one stage at a time.

catalog → composition → SessionPins → RuntimeSeams → githi run → policy → journal → temporal

Everything is real except the edges: a mock sandbox provider (no Docker), and a
non-placeholder test image in place of the D14 placeholder pinned in
``harness/versions/githi-v1.yaml``.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from supervisor.catalog.core.errors import (
    CatalogError,
    DigestMismatch,
    HarnessVersionMismatch,
    NotOffered,
)
from supervisor.catalog.core.types import SessionPins
from supervisor.catalog.loading.bootstrap import (
    agent_platform_root,
    bootstrap_composition_catalog,
    default_catalog_dir,
)
from supervisor.catalog.storage import CompositionCatalog
from supervisor.db.engine import open_db
from supervisor.harness import HarnessCatalog, load_manifest
from supervisor.journal.reader import JournalReader
from supervisor.journal.writer import JournalWriter
from supervisor.session.manager import SessionManager
from supervisor.session.registry import SessionRegistry
from tests.support.session_env import TrackingProvider

HARNESS = "githi-v1"
COMPOSITION = "githi-v1"
MOCK_IMAGE = "sha256:" + hashlib.sha256(b"mock-githi-image").hexdigest()


@dataclass(frozen=True, slots=True)
class Pipeline:
    manager: SessionManager
    compositions: CompositionCatalog
    reader: JournalReader
    sandboxes: TrackingProvider

    def digest_of(self, composition_id: str) -> str:
        return self.compositions.list_versions("composition", composition_id)[-1]


def _shipped_harness_catalog() -> HarnessCatalog:
    """Shipped manifests, with the githi image swapped for a mock (D14 placeholder)."""
    catalog = HarnessCatalog()
    for path in sorted((agent_platform_root() / "harness" / "versions").glob("*.yaml")):
        manifest = load_manifest(path.read_text(encoding="utf-8"))
        if manifest.harness_version == HARNESS:
            manifest = dataclasses.replace(manifest, image_digest=MOCK_IMAGE)
        catalog.register_sync(manifest)
    return catalog


@pytest.fixture
async def pipeline(tmp_path: Path) -> AsyncIterator[Pipeline]:
    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'pipeline.db'}") as (_, factory):
        compositions = bootstrap_composition_catalog(default_catalog_dir())
        sandboxes = TrackingProvider()
        reader = JournalReader(factory, blob_dir=tmp_path / "blobs")
        manager = SessionManager(
            session_factory=factory,
            writer=JournalWriter(factory, blob_dir=tmp_path / "blobs"),
            reader=reader,
            registry=SessionRegistry(),
            catalog=_shipped_harness_catalog(),
            sandbox_provider=sandboxes,
            composition_catalog=compositions,
        )
        yield Pipeline(manager, compositions, reader, sandboxes)


# --- Stage 1: catalog → composition → SessionPins -------------------------------


async def test_session_pins_exact_digests_from_composition(pipeline: Pipeline) -> None:
    digest = pipeline.digest_of(COMPOSITION)
    sid = await pipeline.manager.create(
        tenant_id="t1",
        harness_version=HARNESS,
        composition_id=COMPOSITION,
        composition_digest=digest,
    )

    row = await pipeline.manager._get_session_row(sid)  # noqa: SLF001
    pins = SessionPins.from_dict(row.composition_pins)
    comp = pipeline.compositions.get("composition", COMPOSITION, digest)
    assert pins.composition_digest == digest
    assert pins.harness_version == HARNESS
    assert (pins.prompt_pack_id, pins.prompt_pack_digest) == (
        comp.prompt_pack.id,
        comp.prompt_pack.digest,
    )
    assert (pins.tool_set_id, pins.tool_set_digest) == (comp.tool_set.id, comp.tool_set.digest)
    assert pins.to_dict() == SessionPins.from_dict(pins.to_dict()).to_dict()  # lossless
    assert pipeline.sandboxes.create_calls[-1]["image"] == MOCK_IMAGE
    # The agent persists history under this id; it must be the supervisor's, or resume loses it.
    assert pipeline.manager._registry.get(sid).session_id == sid  # noqa: SLF001


async def test_id_only_create_pins_the_latest_offered_digest(pipeline: Pipeline) -> None:
    sid = await pipeline.manager.create(
        tenant_id="t1", harness_version=HARNESS, composition_id=COMPOSITION
    )
    row = await pipeline.manager._get_session_row(sid)  # noqa: SLF001
    assert row.composition_pins["composition_digest"] == pipeline.digest_of(COMPOSITION)


@pytest.mark.parametrize(
    ("override", "error"),
    [
        pytest.param(lambda p: {"composition_digest": "latest"}, CatalogError, id="floating-ref"),
        pytest.param(
            lambda p: {"composition_digest": p.digest_of("coding-v1")},
            NotOffered,
            id="digest-of-another-composition",
        ),
        pytest.param(
            lambda p: {"harness_version": "coding-v1"},
            HarnessVersionMismatch,
            id="wrong-harness-version",
        ),
    ],
)
async def test_create_refuses_ambiguous_or_mismatched_pins(
    pipeline: Pipeline,
    override: Callable[[Pipeline], dict[str, str]],
    error: type[Exception],
) -> None:
    request = {
        "tenant_id": "t1",
        "harness_version": HARNESS,
        "composition_id": COMPOSITION,
        "composition_digest": pipeline.digest_of(COMPOSITION),
    } | override(pipeline)
    with pytest.raises(error):
        await pipeline.manager.create(**request)


def test_catalog_refuses_a_digest_that_does_not_match_content(pipeline: Pipeline) -> None:
    digest = pipeline.compositions.list_versions("prompt_pack", "coding-prompt")[-1]
    pinned = pipeline.compositions.get("prompt_pack", "coding-prompt", digest)
    forged = dataclasses.replace(pinned, persona="evil")  # keeps the pinned digest
    with pytest.raises(DigestMismatch):
        pipeline.compositions.offer(forged)
    assert pipeline.compositions.get("prompt_pack", pinned.name, pinned.digest).persona == pinned.persona
