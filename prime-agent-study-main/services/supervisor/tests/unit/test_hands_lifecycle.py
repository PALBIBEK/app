"""Sandbox lifecycle: prewarmed as a run begins, kept while used, stopped when idle and
restored on the next need; the profile fields that set it (supervisor.hands.lifecycle)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from supervisor.catalog.core.types import ArtifactStatus, SandboxProfile, SandboxProviderName
from supervisor.catalog.services.publish_checks import _check_lifecycle
from supervisor.catalog.storage.converters import sandbox_profile_from_dict, sandbox_profile_to_dict
from supervisor.hands import HandRecipe
from supervisor.hands.lifecycle import SandboxLifecycle
from tests.unit.test_hands import _hands


async def test_a_prewarm_builds_once_and_the_first_tool_shares_it(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    task = hands.prewarm("s")
    assert task is not None and hands.prewarm("s") is None, "one build at a time"
    await asyncio.sleep(0)  # the run began: its build is under way before any tool asks
    used = await hands.ensure("s")  # the first tool call, while the build runs
    assert await task is used and len(provider.built) == 1
    assert hooks.events == [("up", "sbx-0", "prewarm")]
    assert hands.prewarm("s") is None, "nothing to do once one serves"
    hands.register("none", None)
    assert hands.prewarm("none") is None, "a harness without a sandbox gets none"


async def test_a_failed_prewarm_is_only_logged_and_the_tool_tries_again(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    real = provider.create

    async def broken(**_: object) -> object:
        raise RuntimeError("docker is not running")

    provider.create = broken  # type: ignore[method-assign]
    assert await hands.prewarm("s") is None  # type: ignore[misc]
    provider.create = real  # type: ignore[method-assign]
    assert (await hands.ensure("s")) is not None and hooks.events == [("up", "sbx-0", "first_use")]


async def test_idle_time_counts_from_the_last_use_and_never_under_a_tool(tmp_path: Path) -> None:
    hands, _, _ = _hands(tmp_path)
    assert hands.idle_for("s") == 0.0, "no sandbox, nothing idle"
    await hands.ensure("s")
    later = time.monotonic() + 100
    assert hands.idle_for("s", later) >= 99
    hands.begin_use("s")
    assert hands.in_use("s") and hands.idle_for("s", later) == 0.0
    hands.end_use("s")
    hands.end_use("s")  # an extra end never goes negative
    assert not hands.in_use("s") and hands.idle_for("s", time.monotonic()) < 1


async def test_a_stopped_sandbox_is_rebuilt_as_resumed(tmp_path: Path) -> None:
    hands, provider, hooks = _hands(tmp_path)
    first = await hands.ensure("s")
    assert hands.stop("s") is first and hands.sandbox("s") is None
    again = await hands.ensure("s")
    assert again is not first and len(provider.built) == 2
    assert [e[2] for e in hooks.events] == ["first_use", "resumed"]
    hands.stop("s")
    await hands.prewarm("s")  # type: ignore[misc]
    assert hooks.events[-1][2] == "prewarm_resumed"


async def test_releasing_a_session_cancels_its_prewarm(tmp_path: Path) -> None:
    hands, provider, _ = _hands(tmp_path)
    gate = asyncio.Event()
    real = provider.create

    async def slow(**kw: object) -> object:
        await gate.wait()
        return await real(**kw)

    provider.create = slow  # type: ignore[method-assign]
    task = hands.prewarm("s")
    await asyncio.sleep(0)
    hands.release("s")
    await asyncio.sleep(0)
    assert task is not None and (task.cancelled() or task.done())


def test_profiles_and_the_deployment_decide_start_and_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    default = SandboxLifecycle.from_env()
    plain = HandRecipe(provider=object(), image=None)  # type: ignore[arg-type]
    eager = HandRecipe(provider=object(), image=None, start="with_run", idle_stop_s=0)  # type: ignore[arg-type]
    assert (default.starts_with_run(plain), default.idle_limit(plain)) == (False, 900)
    assert (default.starts_with_run(eager), default.idle_limit(eager)) == (True, 0)
    assert not default.starts_with_run(None) and default.idle_limit(None) == 0
    monkeypatch.setenv("SANDBOX_START", "with_run")
    monkeypatch.setenv("SANDBOX_IDLE_STOP_S", "60")
    env = SandboxLifecycle.from_env()
    assert env.starts_with_run(plain) and env.idle_limit(plain) == 60
    monkeypatch.setenv("SANDBOX_START", "sometimes")
    with pytest.raises(ValueError, match="SANDBOX_START"):
        SandboxLifecycle.from_env()


def _profile(**kw: object) -> SandboxProfile:
    return SandboxProfile(
        name="p", semver="1.0.0", digest="", protocol_version=1, status=ArtifactStatus.OFFERED,
        provider=SandboxProviderName.LOCAL_PROCESS, **kw,  # type: ignore[arg-type]
    )


def test_lifecycle_fields_change_a_profile_only_when_set() -> None:
    plain = _profile()
    assert "start" not in plain.body_for_digest() and "idle_stop_s" not in plain.body_for_digest()
    assert "start" not in sandbox_profile_to_dict(plain), "profiles without them keep their digests"
    set_ = _profile(start="with_run", idle_stop_s=300)
    assert set_.body_for_digest()["start"] == "with_run" and set_.body_for_digest()["idle_stop_s"] == 300
    back = sandbox_profile_from_dict(sandbox_profile_to_dict(set_))
    assert (back.start, back.idle_stop_s) == ("with_run", 300)
    _check_lifecycle("p", set_)
    for bad in (_profile(start="eventually"), _profile(idle_stop_s=-1), _profile(idle_stop_s=100_000)):
        with pytest.raises(Exception, match="start|idle_stop_s"):
            _check_lifecycle("p", bad)
