"""Spawn-time compatibility gate."""

from __future__ import annotations

import pytest

from supervisor.harness import (
    HarnessManifest,
    IncompatibleHarness,
    RecoveryAction,
    RecoveryPolicy,
    RuntimeCapabilities,
)
from supervisor.harness.services.compat_checker import capabilities_for, check
from tests.support.session_env import TrackingProvider, make_manifest


def test_supported_protocol_passes() -> None:
    m = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
    check(m, capabilities_for("echo"), sandbox_provider=None)


def test_unsupported_protocol_names_versions() -> None:
    from dataclasses import replace

    m = make_manifest("bad", requires_sandbox=False, image_digest=None)
    m99 = replace(m, protocol_version=99)
    with pytest.raises(IncompatibleHarness, match="protocol_version=99") as ei:
        check(m99, capabilities_for("echo"))
    assert "supported=" in str(ei.value)


def test_unregistered_runtime_raises() -> None:
    from dataclasses import replace

    m = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
    m2 = replace(m, runtime="nope")
    with pytest.raises(IncompatibleHarness, match="unregistered runtime"):
        check(m2, RuntimeCapabilities(protocol_versions={1}, commands=set()))


def test_missing_required_command_names_command() -> None:
    m = make_manifest("kg-v1", requires_sandbox=False, image_digest=None)
    caps = RuntimeCapabilities(
        protocol_versions={1, 2},
        commands={"prompt", "abort"},  # missing get_state
    )
    with pytest.raises(IncompatibleHarness, match="get_state"):
        check(m, caps)


def test_requires_sandbox_without_provider_raises() -> None:
    m = make_manifest(
        "coding-v1",
        requires_sandbox=True,
        image_digest="sha256:" + "a" * 64,
    )
    with pytest.raises(IncompatibleHarness, match="sandbox"):
        check(m, capabilities_for("echo"), sandbox_provider=None)


@pytest.mark.asyncio
async def test_check_runs_before_provider_call(session_env) -> None:
    from supervisor.session.manager import SessionManager
    from supervisor.session.registry import SessionRegistry
    from dataclasses import replace

    catalog = session_env["catalog"]
    provider = session_env["provider"]
    bad = replace(
        catalog.resolve("coding-v1"),
        protocol_version=99,
    )
    catalog.register(bad)

    m = SessionManager(
        session_factory=session_env["factory"],
        writer=session_env["writer"],
        reader=session_env["reader"],
        registry=SessionRegistry(),
        catalog=catalog,
        sandbox_provider=provider,
    )
    with pytest.raises(IncompatibleHarness, match="protocol_version=99"):
        await m.create(
            tenant_id="t1", harness_version="coding-v1", runtime_name="echo"
        )
    assert provider.create_calls == []
