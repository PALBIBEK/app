"""Every catalog field does something: what each one changes, checked one at a time.

A field a publisher can set and nothing reads is a promise the platform breaks
silently. Each test here pins down what a field now does: refused at publish,
enforced at session creation, or shown back.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from supervisor.catalog import (
    SUPPORTED_PROTOCOL_VERSIONS,
    AgentComposition,
    ArtifactKind,
    ArtifactMetadata,
    ArtifactStatus,
    CatalogOwnershipRequired,
    CompositionCatalog,
    CompositionDigestRequired,
    CompositionIncompatible,
    DigestMismatch,
    DigestRef,
    FloatingRef,
    InvalidArtifact,
    InvalidDigest,
    RecallBackend,
    SandboxProfile,
    SandboxProviderName,
    SessionPins,
    artifact_from_dict,
    artifact_to_dict,
    content_digest,
    reject_floating_ref,
    validate_digest_format,
    verify_digest,
)
from supervisor.catalog.loading.bootstrap import default_catalog_dir
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.catalog.services import (
    check_publishable,
    require_sticky_compatible,
    require_tenant_access,
    resolve_composition,
)
from supervisor.catalog.services.resolver import composition_digest_required
from supervisor.catalog.views import pinned_view

GOOD = "sha256:" + "ab" * 32


def _shipped(kind_dir: str, name: str) -> dict[str, Any]:
    return json.loads((default_catalog_dir() / kind_dir / f"{name}.json").read_text())


# --- digests and references ------------------------------------------------------------------


def test_a_digest_is_sha256_and_64_lowercase_hex() -> None:
    validate_digest_format(GOOD)
    for bad in ("", "md5:" + "ab" * 32, "sha256:" + "AB" * 32, "sha256:abc", "sha256:" + "zz" * 32):
        with pytest.raises(InvalidDigest):
            validate_digest_format(bad)


def test_a_reference_that_could_move_is_refused() -> None:
    reject_floating_ref("githi-v1")
    reject_floating_ref(GOOD)
    for floating in ("", "latest", "githi-v1:latest", "sha256:abc", "sha256:" + "zz" * 32):
        with pytest.raises(FloatingRef):
            reject_floating_ref(floating)


def test_verify_digest_returns_it_or_names_the_real_one() -> None:
    body = {"a": 1}
    assert verify_digest(body, content_digest(body)) == content_digest(body)
    with pytest.raises(DigestMismatch):
        verify_digest(body, GOOD)


# --- metadata: assigned by the catalog, never read from a body -------------------------------


def test_a_body_cannot_claim_who_published_it() -> None:
    body = _shipped("prompt_packs", "coding-prompt") | {
        "metadata": {"created_by": "someone-else", "created_at": "2020-01-01T00:00:00", "updated_at": "2020-01-01T00:00:00"}
    }
    pack = artifact_from_dict("prompt_pack", body)
    assert pack.metadata is None
    assert "metadata" not in artifact_to_dict(pack)  # the body is content; metadata is reported beside it
    check_publishable("prompt_pack", body, pack)  # a body copied from a read (with metadata) still publishes


def test_offer_attaches_the_rows_metadata_and_deprecate_keeps_it() -> None:
    catalog = CompositionCatalog()
    at = datetime(2026, 10, 1, tzinfo=UTC)
    meta = ArtifactMetadata(created_by="alice", created_at=at, updated_at=at, owner_id="t1")
    pack = catalog.offer(artifact_from_dict("prompt_pack", _shipped("prompt_packs", "coding-prompt")), metadata=meta)
    assert pack.metadata == meta
    later = replace(meta, deprecated_by="bob", deprecated_at=at)
    gone = catalog.deprecate("prompt_pack", pack.name, pack.digest, metadata=later)
    assert gone.status is ArtifactStatus.DEPRECATED and gone.metadata == later
    assert later.to_dict()["deprecated_by"] == "bob"


# --- what publish refuses ----------------------------------------------------------------------


def test_an_artifact_schema_this_supervisor_cannot_run_is_refused() -> None:
    body = _shipped("prompt_packs", "coding-prompt")
    newer = max(SUPPORTED_PROTOCOL_VERSIONS) + 1
    with pytest.raises(InvalidArtifact, match=f"protocol_version {newer}"):
        check_publishable("prompt_pack", body | {"protocol_version": newer}, artifact_from_dict("prompt_pack", body | {"protocol_version": newer}))


def test_a_prompt_section_marked_unfinished_is_refused() -> None:
    body = _shipped("prompt_packs", "coding-prompt")
    body["sections"][0]["complete"] = False
    with pytest.raises(InvalidArtifact, match="unfinished"):
        check_publishable("prompt_pack", body, artifact_from_dict("prompt_pack", body))


def test_recall_max_tokens_is_read_and_only_changes_the_digest_when_set() -> None:
    body = _shipped("memory_profiles", "durable-recall")
    plain = artifact_from_dict("memory_profile", body)
    assert plain.recall_max_tokens is None
    assert content_digest(plain.body_for_digest()) == body["digest"]  # shipped digests are unchanged
    capped = artifact_from_dict("memory_profile", body | {"recall": body["recall"] | {"max_tokens": 300}})
    assert capped.recall_max_tokens == 300
    assert artifact_from_dict("memory_profile", artifact_to_dict(capped)).recall_max_tokens == 300


def test_the_memory_factory_honours_recall_max_tokens() -> None:
    from supervisor.seams.core.factory import (
        DEFAULT_RECALL_MAX_TOKENS,
        build_context_memory_from_profile,
    )
    from supervisor.seams.providers.gateway.fake_gateway import FakeModelGatewayClient

    profile = artifact_from_dict("memory_profile", _shipped("memory_profiles", "durable-recall"))
    assert profile.recall_backend in (RecallBackend.DURABLE, RecallBackend.MEMORY_SERVICE)

    def recall_budget(p: Any) -> int:
        memory = build_context_memory_from_profile(p, FakeModelGatewayClient())
        return int(memory._recall._max_tokens)  # the durable recall inside the composite

    assert recall_budget(profile) == DEFAULT_RECALL_MAX_TOKENS
    assert recall_budget(replace(profile, recall_max_tokens=300)) == 300


# --- drafts are not offered ----------------------------------------------------------------------


def test_a_shipped_file_marked_draft_is_not_offered(tmp_path: Path) -> None:
    for sub in ("prompt_packs", "tool_sets", "memory_profiles", "compositions"):
        (tmp_path / sub).mkdir()
    pack = _shipped("prompt_packs", "coding-prompt")
    (tmp_path / "prompt_packs" / "p.json").write_text(json.dumps(pack))
    (tmp_path / "prompt_packs" / "wip.json").write_text(json.dumps(pack | {"name": "wip", "status": "draft"}))
    catalog = load_composition_catalog(tmp_path)
    assert catalog.list_versions("prompt_pack", "coding-prompt")
    assert not catalog.list_versions("prompt_pack", "wip")


def test_a_shipped_composition_keeps_its_named_hands(tmp_path: Path) -> None:
    for sub in ("prompt_packs", "tool_sets", "sandbox_profiles", "compositions"):
        (tmp_path / sub).mkdir()
    (tmp_path / "prompt_packs" / "p.json").write_text(json.dumps(_shipped("prompt_packs", "coding-prompt")))
    (tmp_path / "tool_sets" / "t.json").write_text(json.dumps(_shipped("tool_sets", "githi-tools")))
    (tmp_path / "sandbox_profiles" / "s.json").write_text(json.dumps(_shipped("sandbox_profiles", "githi-docker-v1")))
    comp = {
        "name": "two-hands",
        "semver": "1.0.0",
        "harness_version": "githi-v1",
        "prompt_pack": {"id": "coding-prompt", "digest": "sha256:REPLACE"},
        "tool_set": {"id": "githi-tools", "digest": "sha256:REPLACE"},
        "sandbox_profile": {"id": "githi-docker-v1", "digest": "sha256:REPLACE"},
        "hands": {"build": {"id": "githi-docker-v1", "digest": "sha256:REPLACE"}},
    }
    (tmp_path / "compositions" / "c.json").write_text(json.dumps(comp))
    loaded = load_composition_catalog(tmp_path).latest("composition", "two-hands")
    assert isinstance(loaded, AgentComposition)
    assert [name for name, _ in loaded.hands] == ["build"]  # the old loader dropped hands


# --- sticky harnesses and sandboxes that may not be kept -------------------------------------------


def _catalog_with_profile(*, sticky_eligible: bool) -> tuple[CompositionCatalog, AgentComposition]:
    catalog = load_composition_catalog(default_catalog_dir())
    shipped = artifact_from_dict("sandbox_profile", _shipped("sandbox_profiles", "githi-docker-v1"))
    assert isinstance(shipped, SandboxProfile)
    profile = catalog.offer(
        replace(shipped, name="no-keep", digest="", sticky_eligible=sticky_eligible), owner="t1"
    )
    base = catalog.latest("composition", "githi-tools-v1")
    assert isinstance(base, AgentComposition)
    comp = replace(base, name="c", digest="", sandbox_profile=DigestRef(profile.name, profile.digest))
    return catalog, comp


def test_a_sticky_harness_cannot_pin_a_sandbox_that_may_not_be_kept() -> None:
    catalog, comp = _catalog_with_profile(sticky_eligible=False)
    with pytest.raises(CompositionIncompatible, match="sticky_eligible"):
        require_sticky_compatible(catalog, comp, sticky=True)
    require_sticky_compatible(catalog, comp, sticky=False)  # a stateless harness tears it down anyway

    keep, ok = _catalog_with_profile(sticky_eligible=True)
    require_sticky_compatible(keep, ok, sticky=True)


def test_a_named_hand_is_held_to_the_same_rule() -> None:
    catalog, comp = _catalog_with_profile(sticky_eligible=False)
    default = catalog.latest("sandbox_profile", "githi-docker-v1")
    assert default is not None
    with_hand = replace(
        comp, sandbox_profile=DigestRef(default.name, default.digest), hands=(("build", comp.sandbox_profile),)
    )
    with pytest.raises(CompositionIncompatible):
        require_sticky_compatible(catalog, with_hand, sticky=True)


# --- resolution ----------------------------------------------------------------------------------------


def test_a_deployment_can_require_exact_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = load_composition_catalog(default_catalog_dir())
    comp = catalog.latest("composition", "githi-v1")
    assert comp is not None
    monkeypatch.delenv("REQUIRE_COMPOSITION_DIGEST", raising=False)
    assert not composition_digest_required()
    assert resolve_composition(catalog, composition_id="githi-v1").composition_digest == comp.digest

    monkeypatch.setenv("REQUIRE_COMPOSITION_DIGEST", "true")
    assert composition_digest_required()
    with pytest.raises(CompositionDigestRequired):
        resolve_composition(catalog, composition_id="githi-v1", require_digest=composition_digest_required())
    exact = resolve_composition(
        catalog, composition_id="githi-v1", composition_digest=comp.digest, require_digest=True
    )
    assert exact.composition_digest == comp.digest


def test_pins_list_every_part_including_hands() -> None:
    pins = SessionPins(
        composition_id="c", composition_digest="d0", harness_version="h",
        prompt_pack_id="p", prompt_pack_digest="d1", tool_set_id="t", tool_set_digest="d2",
        sandbox_profile_id="s", sandbox_profile_digest="d3", memory_profile_id="m", memory_profile_digest="d4",
        hands=(("build", "s2", "d5"),),
    )
    assert list(pins.refs()) == [
        (ArtifactKind.COMPOSITION, "c", "d0"),
        (ArtifactKind.PROMPT_PACK, "p", "d1"),
        (ArtifactKind.TOOL_SET, "t", "d2"),
        (ArtifactKind.SANDBOX_PROFILE, "s", "d3"),
        (ArtifactKind.SANDBOX_PROFILE, "s2", "d5"),
        (ArtifactKind.MEMORY_PROFILE, "m", "d4"),
    ]


def test_another_tenants_hand_is_not_offered_to_you() -> None:
    catalog, _ = _catalog_with_profile(sticky_eligible=True)  # "no-keep" is t1's
    pins = resolve_composition(catalog, composition_id="githi-tools-v1")
    mine = replace(pins, hands=(("build", "no-keep", catalog.latest("sandbox_profile", "no-keep").digest),))  # type: ignore[union-attr]
    require_tenant_access(catalog, mine, "t1")
    with pytest.raises(Exception, match="not offered|NOT_OFFERED|no-keep"):
        require_tenant_access(catalog, mine, "t2")


# --- what a session runs, read back -------------------------------------------------------------------


def test_the_pinned_view_names_every_part_and_its_settings() -> None:
    catalog = load_composition_catalog(default_catalog_dir())
    view = pinned_view(catalog, resolve_composition(catalog, composition_id="githi-tools-v1"))
    roles = {p.role: p for p in view.parts}
    assert set(roles) == {"prompt", "tools", "hand:default", "memory"}
    assert roles["hand:default"].settings["provider"] == SandboxProviderName.LOCAL_DOCKER.value
    assert roles["hand:default"].settings["sticky_eligible"] is True
    assert {t["name"] for t in roles["tools"].settings["tools"]} >= {"bash", "read"}
    assert all(p.status == "offered" and p.semver for p in view.parts)
    assert view.composition_status == "offered" and view.model_route
    assert not hasattr(view, "gateway_base_url")  # internal addresses are not shown, only whether set


# --- errors --------------------------------------------------------------------------------------------


def test_a_taken_name_does_not_say_whose_it_is() -> None:
    other = CatalogOwnershipRequired(principal_id="t2", artifact_kind="prompt_pack", artifact_id="x", owner_id="t1")
    assert "t1" not in str(other) and "t1" not in json.dumps(other.to_dict())
    shipped = CatalogOwnershipRequired(principal_id="t2", artifact_kind="prompt_pack", artifact_id="x", owner_id="system")
    assert "shipped" in str(shipped) and shipped.to_dict()["detail"]["owner_id"] == "system"
    assert set(other.to_dict()) == {"code", "message", "retryable", "detail"}
