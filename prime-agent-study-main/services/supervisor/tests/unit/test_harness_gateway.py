"""Phase 2 step 3 — harness runtime routes LLM via model gateway."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from supervisor.agent.factory import create_runtime
from supervisor.agent.seams import RuntimeSeams
from supervisor.catalog.binder import bind_runtime_seams
from supervisor.catalog.resolve import resolve_composition
from supervisor.catalog.store import CompositionCatalog
from supervisor.catalog.types import AgentComposition, DigestRef, PlugBindings
from supervisor.harness import materialize_harness_binds
from supervisor.seams.factory import build_runtime_seams
from supervisor.seams.providers.fake_gateway import FakeModelGatewayClient
from tests.support.replay import ReplaySandbox
from tests.unit.test_catalog_pins import _sample_prompt_pack, _sample_tool_set


@pytest.fixture
def harness_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CLI", "mock-harness-agent")


def _offer_gateway_composition(cat: CompositionCatalog) -> AgentComposition:
    pack = cat.offer(_sample_prompt_pack())
    tools = cat.offer(_sample_tool_set())
    comp = AgentComposition(
        name="gateway-comp",
        semver="1.0.0",
        digest="",
        protocol_version=1,
        status="offered",
        harness_version="coding-v1",
        prompt_pack=DigestRef(id=pack.name, digest=pack.digest),
        tool_set=DigestRef(id=tools.name, digest=tools.digest),
        plug_bindings=PlugBindings(
            gateway_base_url="http://gateway.test:8081",
            model_route="route/sonnet",
        ),
    )
    return cat.offer(comp)


def test_build_spawn_plan_uses_gateway_from_plug_bindings(
    tmp_path: Path, harness_cli: None
) -> None:
    from supervisor.harness import build_spawn_plan

    yal = tmp_path / ".yal"
    yal.mkdir()
    (yal / "launch.json").write_text(json.dumps({"cli": "mock-harness-agent"}) + "\n")
    (yal / "plug_bindings.json").write_text(
        json.dumps(
            {
                "gateway_base_url": "http://gateway.test:8081",
                "model_route": "anthropic/claude-sonnet-4",
            }
        )
        + "\n"
    )
    seams = build_runtime_seams(gateway_base_url="http://gateway.test:8081")
    plan = build_spawn_plan(
        session_dir=str(tmp_path),
        model=None,
        provider=None,
        seams=seams,
    )
    assert plan.argv[0] == "mock-harness-agent"
    assert plan.argv[plan.argv.index("--provider") + 1] == "openai"
    assert plan.argv[plan.argv.index("--model") + 1] == "anthropic/claude-sonnet-4"
    assert plan.env["OPENAI_BASE_URL"] == "http://gateway.test:8081/v1"


def test_build_spawn_plan_refuses_fake_gateway_with_plug_bindings(
    tmp_path: Path, harness_cli: None
) -> None:
    from supervisor.harness import build_spawn_plan

    yal = tmp_path / ".yal"
    yal.mkdir()
    (yal / "plug_bindings.json").write_text(
        json.dumps({"gateway_base_url": "http://gateway.test:8081"}) + "\n"
    )
    seams = RuntimeSeams(model_gateway=FakeModelGatewayClient())
    with pytest.raises(ValueError, match="FakeModelGatewayClient"):
        build_spawn_plan(session_dir=str(tmp_path), model=None, provider=None, seams=seams)


@pytest.mark.asyncio
async def test_harness_runtime_spawn_uses_gateway_argv(
    tmp_path: Path, harness_cli: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RecordingSandbox(ReplaySandbox):
        def __init__(self) -> None:
            super().__init__()
            self.spawn_cmds: list[list[str]] = []
            self.spawn_envs: list[dict[str, str] | None] = []

        async def spawn(self, cmd, *, cwd=None, env=None):  # type: ignore[no-untyped-def]
            self.spawn_cmds.append(list(cmd))
            self.spawn_envs.append(dict(env) if env else None)
            return await super().spawn(cmd, cwd=cwd, env=env)

    cat = CompositionCatalog()
    comp = _offer_gateway_composition(cat)
    pins = resolve_composition(cat, composition_id=comp.name)
    seams = bind_runtime_seams(pins, cat)
    monkeypatch.setenv("HARNESS_CLI", "mock-harness-agent")
    materialize_harness_binds(tmp_path, pins, seams)

    sandbox = RecordingSandbox()
    rt = create_runtime("harness", seams=seams)
    await rt.start(sandbox, str(tmp_path))
    assert sandbox.spawn_cmds
    cmd = sandbox.spawn_cmds[0]
    assert cmd[0] == "mock-harness-agent"
    assert cmd[cmd.index("--provider") + 1] == "openai"
    assert cmd[cmd.index("--model") + 1] == "route/sonnet"
    assert sandbox.spawn_envs[0] is not None
    assert sandbox.spawn_envs[0]["OPENAI_BASE_URL"] == "http://gateway.test:8081/v1"
    await rt.stop()
