"""The browser code mode in a real githi session: a composition built from the seams
(the browser tool set, a sandbox profile naming its browser), a durable run on Temporal,
the model calling ``browser_exec``, and the browser running in the session's sandbox.

And the fail-closed rule: a profile asking for a network its provider cannot enforce is
refused when the session is made, before anything runs."""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from supervisor.agent.events import ToolEnd
from supervisor.app import SupervisorState
from supervisor.catalog import (
    DigestRef,
    FileEffects,
    NetworkAccess,
    NetworkMode,
    SandboxProviderName,
)
from supervisor.catalog.repository import persist_offer
from supervisor.temporal.contracts import CreateSession, Message
from supervisor.temporal.worker import session_worker
from tests.support.browser import BROWSERS_ENV, kill, needs_browser
from tests.support.githi_env import Env, payloads, run_ended
from tests.support.scripted import ScriptedGateway

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.usefixtures("memory_service_url"), needs_browser]


async def _browser_session(env: Env, state: SupervisorState, **profile_changes: Any) -> str:
    """A session on the shipped composition, with the browser tool set and a host-process
    sandbox profile changed by ``profile_changes``. A host process isolates nothing, so a
    tool that acts outside the workspace runs there only on ``danger_full_access``."""
    catalog = state.composition_catalog
    assert catalog is not None
    latest = lambda kind, name: catalog.get(kind, name, catalog.list_versions(kind, name)[-1])
    base, tools = latest("composition", "githi-v1"), latest("tool_set", "githi-browser-tools")
    profile = catalog.offer(
        dataclasses.replace(
            latest("sandbox_profile", "githi-docker-v1"),
            name=f"browser-{uuid.uuid4().hex[:6]}",
            digest="",
            provider=SandboxProviderName.LOCAL_PROCESS,
            image_digest=f"sha256:{hashlib.sha256(b'host').hexdigest()}",
            file_effects=FileEffects.DANGER_FULL_ACCESS,
            **profile_changes,
        )
    )
    await persist_offer(env.factory, catalog, "sandbox_profile", profile.name, profile.digest)  # type: ignore[arg-type]
    composition = catalog.offer(
        dataclasses.replace(
            base,
            name=f"browser-agent-{uuid.uuid4().hex[:6]}",
            digest="",
            tool_set=DigestRef(id=tools.name, digest=tools.digest),
            sandbox_profile=DigestRef(id=profile.name, digest=profile.digest),
        )
    )
    await persist_offer(env.factory, catalog, "composition", composition.name, composition.digest)  # type: ignore[arg-type]
    sid = uuid.uuid4().hex
    await env.sessions.create(
        sid, CreateSession("t1", "githi-v1", composition_id=composition.name, composition_digest=composition.digest, principal_id="alice")
    )
    return sid


@pytest.fixture
def default_browsers() -> Iterator[Path]:
    """A host-process sandbox runs commands with a scrubbed environment and the real HOME,
    so its runner finds Playwright's browsers where Playwright looks by default: point that
    at this machine's for the test (a sandbox image has its own). The browser home the
    runner then uses is its default; the test stops only the browsers it started there."""
    default = Path.home() / ".cache" / "ms-playwright"
    made = not default.exists()
    if made:
        default.parent.mkdir(parents=True, exist_ok=True)
        default.symlink_to(BROWSERS_ENV["PLAYWRIGHT_BROWSERS_PATH"])
    home = Path("/tmp/githi-browser")
    before = {p.read_text() for p in home.glob("*/daemon.pid")}
    yield home
    for pid_file in home.glob("*/daemon.pid"):
        if pid_file.read_text() not in before:
            kill(int(pid_file.read_text()))
    if made:
        default.unlink()


async def test_a_browser_agent_built_from_the_seams_browses_in_its_sandbox(
    env: Env, model: Callable[..., ScriptedGateway], default_browsers: Path
) -> None:
    code = "page.set_content('<title>from the sandbox</title><button>b</button>')\nprint(page.title())\nprint(snapshot())"
    model(("browser_exec", {"code": code}))
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await _browser_session(env, state, browser="sandbox-chromium")
        await env.sessions.submit(sid, Message("c1", "look at the page"))
        events = await env.wait_for(state, sid, run_ended(), timeout=60)

    (end,) = [e for e in payloads(events, ToolEnd) if e.tool_name == "browser_exec"]
    result = end.result["payload"]
    assert not end.is_error and result["exit_code"] == 0, end.result
    assert result["output"].startswith("from the sandbox\n"), result
    assert "button: b" in result["output"]


async def test_a_profile_whose_network_its_provider_cannot_enforce_is_refused(env: Env) -> None:
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        with pytest.raises(Exception, match="cannot enforce network 'open'"):
            await _browser_session(env, state, network=NetworkAccess(NetworkMode.OPEN))
