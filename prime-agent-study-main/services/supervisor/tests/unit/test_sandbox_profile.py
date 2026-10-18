"""sandbox-profile — catalog SandboxProfile + per-composition override (R/G/R)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from supervisor.catalog.types import AgentComposition, DigestRef, SandboxProfile
from supervisor.harness import HarnessManifest, IncompatibleHarness
from supervisor.sandbox.resolve import (
    assert_sandbox_profile_compatible,
    resolve_sandbox_create,
)


def _sample_profile(*, name: str = "coding-sbx", provider: str = "none") -> SandboxProfile:
    return SandboxProfile(
        name=name,
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        provider=provider,  # type: ignore[arg-type]
        file_effects="workspace_write",
        approval_default="ask",
        sticky_eligible=True,
        image_digest="sha256:profile-image",
    )


def _manifest(*, requires_sandbox: bool = True) -> HarnessManifest:
    from tests.support.session_env import make_manifest

    return make_manifest(
        "coding-v1",
        requires_sandbox=requires_sandbox,
        image_digest="sha256:" + "a" * 64 if requires_sandbox else None,
    )


def test_sp_u1_offer_sandbox_profile_round_trip() -> None:
    from supervisor.catalog.store import CompositionCatalog

    cat = CompositionCatalog()
    offered = cat.offer(_sample_profile(provider="local_process"))
    assert offered.digest.startswith("sha256:")
    got = cat.get("sandbox_profile", offered.name, offered.digest)
    assert isinstance(got, SandboxProfile)
    assert got.provider == "local_process"


def test_sp_u2_composition_with_profile_resolves_pins() -> None:
    from supervisor.catalog.resolve import resolve_composition
    from supervisor.catalog.store import CompositionCatalog

    from tests.unit.test_catalog_pins import _offer_composition

    cat = CompositionCatalog()
    prof = cat.offer(_sample_profile(provider="local_docker"))
    comp, _pack, _tools = _offer_composition(cat)
    comp2 = AgentComposition(
        name=comp.name,
        semver=comp.semver,
        digest=comp.digest,
        protocol_version=comp.protocol_version,
        status=comp.status,
        harness_version=comp.harness_version,
        prompt_pack=comp.prompt_pack,
        tool_set=comp.tool_set,
        sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
    )
    comp3 = cat.offer(comp2)
    pins = resolve_composition(cat, composition_id=comp3.name)
    assert pins.sandbox_profile_id == prof.name
    assert pins.sandbox_profile_digest == prof.digest


def test_sp_u3_profile_overrides_deploy_provider() -> None:
    from supervisor.sandbox.none import NoneProvider

    manifest = _manifest(requires_sandbox=False)
    profile = _sample_profile(provider="none")
    provider, image = resolve_sandbox_create(
        manifest,
        deploy_provider=None,
        profile=profile,
    )
    assert provider is None
    assert image is None

    manifest2 = _manifest(requires_sandbox=True)
    profile2 = _sample_profile(provider="local_process")
    with patch(
        "supervisor.sandbox.resolve.create_sandbox_provider",
        return_value=NoneProvider(),
    ) as mock_create:
        provider2, image2 = resolve_sandbox_create(
            manifest2,
            deploy_provider=None,
            profile=profile2,
        )
        mock_create.assert_called_once_with("local_process", client=None)
    assert provider2 is not None
    assert image2 == "sha256:profile-image"


def test_sp_u4_profile_image_overrides_manifest() -> None:
    from supervisor.sandbox.none import NoneProvider

    manifest = _manifest()
    profile = _sample_profile(provider="none")
    profile = SandboxProfile(
        name=profile.name,
        semver=profile.semver,
        digest=profile.digest,
        protocol_version=profile.protocol_version,
        status=profile.status,
        provider="local_process",  # type: ignore[arg-type]
        image_digest="sha256:override",
    )
    with patch(
        "supervisor.sandbox.resolve.create_sandbox_provider",
        return_value=NoneProvider(),
    ):
        _provider, image = resolve_sandbox_create(
            manifest,
            deploy_provider=None,
            profile=profile,
        )
    assert image == "sha256:override"


def test_sp_u5_requires_sandbox_with_none_profile_fails() -> None:
    manifest = _manifest(requires_sandbox=True)
    profile = _sample_profile(provider="none")
    with pytest.raises(IncompatibleHarness):
        assert_sandbox_profile_compatible(manifest, profile)


def test_sp_u6_materialize_sandbox_profile_json(tmp_path: Path) -> None:
    from supervisor.harness import materialize_harness_binds
    from supervisor.seams.factory import build_runtime_seams
    from supervisor.catalog.types import SessionPins

    prof = _sample_profile(provider="local_docker")
    pins = SessionPins(
        composition_id="c1",
        composition_digest="sha256:comp",
        harness_version="coding-v1",
        prompt_pack_id="p1",
        prompt_pack_digest="sha256:pp",
        tool_set_id="t1",
        tool_set_digest="sha256:ts",
        sandbox_profile_id=prof.name,
        sandbox_profile_digest=prof.digest,
    )
    seams = build_runtime_seams()
    yal = materialize_harness_binds(tmp_path, pins, seams, sandbox_profile=prof)
    data = (yal / "sandbox_profile.json").read_text()
    assert '"provider": "local_docker"' in data
    assert prof.digest in data


@pytest.mark.asyncio
async def test_sp_i1_session_create_uses_profile_provider(session_env) -> None:
    from supervisor.catalog.store import CompositionCatalog
    from tests.unit.test_catalog_pins import _offer_composition

    cat = CompositionCatalog()
    prof = cat.offer(_sample_profile(provider="local_process"))
    comp, _p, _t = _offer_composition(cat)
    comp2 = AgentComposition(
        name="with-sbx",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        harness_version="coding-v1",
        prompt_pack=comp.prompt_pack,
        tool_set=comp.tool_set,
        sandbox_profile=DigestRef(id=prof.name, digest=prof.digest),
    )
    comp3 = cat.offer(comp2)
    session_env["manager"]._composition_catalog = cat

    with patch(
        "supervisor.sandbox.resolve.create_sandbox_provider",
        return_value=session_env["provider"],
    ) as mock_create:
        sid = await session_env["manager"].create(
            tenant_id="t1",
            harness_version="coding-v1",
            runtime_name="echo",
            composition_id=comp3.name,
        )
        mock_create.assert_called_once_with("local_process", client=None)

    row = await session_env["manager"]._get_session_row(sid)
    assert row.composition_pins is not None
    assert row.composition_pins.get("sandbox_profile_id") == prof.name
    assert len(session_env["provider"].create_calls) == 1


def test_sp_c1_contract_exists() -> None:
    root = Path(__file__).resolve().parents[4]
    assert (root / "contracts" / "sandbox_profile.md").is_file()


def test_sp_a1_no_provider_leaks() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "supervisor"
    for folder in ("session", "journal", "api"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text().lower()
            for word in ("docker", "e2b"):
                if word in text:
                    pytest.fail(f"provider leak in {path}: {word!r}")
