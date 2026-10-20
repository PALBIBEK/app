"""The supervisor's routed model access against the real model-gateway service.

Discovery, the three wire protocols, native reasoning replayed across tool calls
(the gateway's echo rejects a forged signature or stripped encrypted reasoning,
as the vendors do), session keys and budgets, Ultrafast, a whole githi session
turn, and the browse runner's model endpoint.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from supervisor.agent.events import MessageEnd, ToolEnd
from supervisor.app import SupervisorState
from supervisor.catalog.core.types import DigestRef, FileEffects, SandboxProviderName
from supervisor.catalog.repository import persist_offer
from supervisor.models import (
    GatewayConfig,
    ModelCallError,
    ModelRouter,
    RoutedModelClient,
    reset_model_router,
    use_model_router,
)
from supervisor.models.browser import BrowserAccess, BrowserCapability, BrowserGrant
from supervisor.seams.core.types import ChatMessage
from supervisor.seams.providers.gateway.virtual_key_client import HttpVirtualKeyClient
from supervisor.temporal.contracts import CreateSession, Message
from supervisor.temporal.worker import session_worker
from tests.support.githi_env import FAST, Env, payloads, run_ended
from tests.support.live_gateway import LiveGateway, serve_gateway

READ = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[LiveGateway]:
    with serve_gateway(monkeypatch) as live:
        yield live


@pytest.fixture
def router(gateway: LiveGateway) -> Iterator[ModelRouter]:
    router = ModelRouter(
        GatewayConfig.from_env(gateway.env(**{"gpt-6-astra-ultrafast": "gpt-6-astra"}))
    )  # type: ignore[arg-type]
    use_model_router(router)
    yield router
    reset_model_router()


def _tool_loop(client: RoutedModelClient, model: str) -> tuple[ChatMessage, ChatMessage]:
    """Ask for a tool call, answer it, and return (the call turn, the final turn)."""
    history = [
        ChatMessage("system", "be brief"),
        ChatMessage("user", '/tool read {"path": "a.txt"}'),
    ]
    first = client.complete(history, [READ], model=model)
    assert first.finish_reason == "tool_calls", first
    (call,) = first.message.tool_calls or []
    history += [
        first.message,
        ChatMessage("tool", '{"ok": true, "payload": "A"}', tool_call_id=call["id"]),
    ]
    second = client.complete(history, [READ], model=model)
    assert second.finish_reason == "stop"
    return first.message, second.message


def test_discovery_finds_every_group_with_its_protocol(router: ModelRouter) -> None:
    models = {spec.id: spec.api.value for spec in router.selectable_models()}
    assert models == {
        "gateway/claude": "anthropic-messages",
        "gateway/gpt": "openai-responses",
        "gateway/mix": "openai-completions",
        # The picker offers the native alias in place of the group it routes to.
        "gpt-6-astra-ultrafast": "openai-responses",
    }
    assert router.resolve("gpt-6-astra-ultrafast").pricing.input == 60


@pytest.mark.parametrize(
    ("model", "reasoning"),
    [("gateway/claude", True), ("gateway/gpt", True), ("gateway/mix", False)],
)
def test_a_tool_loop_round_trips_on_every_protocol(
    router: ModelRouter, model: str, reasoning: bool
) -> None:
    client = RoutedModelClient(router, session_id="s-1")
    call_turn, final = _tool_loop(client, model)
    kinds = [p["type"] for p in call_turn.parts or []]
    assert kinds == (["reasoning", "tool_call"] if reasoning else ["tool_call"])
    if reasoning:
        assert call_turn.parts and call_turn.parts[0]["signature"]
    assert final.content == 'echo:{"ok": true, "payload": "A"}'


def test_forged_native_reasoning_is_refused_and_never_retried(router: ModelRouter) -> None:
    client = RoutedModelClient(router)
    for model in ("gateway/claude", "gateway/gpt"):
        call_turn, _ = _tool_loop(client, model)
        forged = [dict(p) for p in call_turn.parts or []]
        forged[0]["signature"] = (
            "forged"
            if model == "gateway/claude"
            else json.dumps({**json.loads(forged[0]["signature"]), "encrypted_content": "forged"})
        )
        tampered = ChatMessage(
            "assistant", "", tool_calls=call_turn.tool_calls, parts=forged, model=model
        )
        history = [
            ChatMessage("user", '/tool read {"path": "a.txt"}'),
            tampered,
            ChatMessage("tool", "{}", tool_call_id=(call_turn.tool_calls or [{}])[0]["id"]),
        ]
        with pytest.raises(ModelCallError) as caught:
            client.complete(history, [READ], model=model)
        assert caught.value.status == 400 and caught.value.retryable is False


def test_reasoning_from_another_model_is_shown_as_text(router: ModelRouter) -> None:
    client = RoutedModelClient(router)
    call_turn, _ = _tool_loop(client, "gateway/claude")
    history = [
        ChatMessage("user", '/tool read {"path": "a.txt"}'),
        call_turn,
        ChatMessage("tool", "{}", tool_call_id=(call_turn.tool_calls or [{}])[0]["id"]),
    ]
    answer = client.complete(
        history, [READ], model="gateway/gpt"
    )  # no Anthropic signature reaches OpenAI
    assert answer.finish_reason == "stop"


def test_ultrafast_reaches_its_base_group_on_the_responses_wire(router: ModelRouter) -> None:
    client = RoutedModelClient(router)
    spec, turn = client.turn([ChatMessage("user", "go")], [], model="gpt-6-astra-ultrafast")
    assert turn.service_tier == "ultrafast"
    assert spec.id == "gpt-6-astra-ultrafast"
    assert turn.cost == spec.pricing.cost(turn.usage)  # its own 6x card, not a tier multiplier
    assert client.context_window("gpt-6-astra-ultrafast") == 1_050_000


def test_a_session_key_is_scoped_to_its_group_and_its_budget(
    router: ModelRouter, gateway: LiveGateway
) -> None:
    keys = HttpVirtualKeyClient(base_url=gateway.url, service_token=gateway.token)
    target = router.target_for("gateway/claude")
    assert target == "claude"
    minted = keys.mint(
        tenant_id="t",
        session_id="s-1",
        principal_id="p",
        allowed_models=[target],
        max_budget_usd=1e-6,
    )
    client = RoutedModelClient(router, api_key=minted.token, session_id="s-1")
    client.complete([ChatMessage("user", "one two three four five")], [], model="gateway/claude")
    with pytest.raises(ModelCallError) as spent:
        client.complete([ChatMessage("user", "again")], [], model="gateway/claude")
    assert spent.value.status == 429 and spent.value.code == "VIRTUAL_KEY_BUDGET_EXCEEDED"
    assert spent.value.retryable is False
    with pytest.raises(ModelCallError) as denied:
        client.complete([ChatMessage("user", "x")], [], model="gateway/gpt")
    assert denied.value.status == 403


# -- a whole session turn ----------------------------------------------------------


async def create_on(
    env: Env,
    state: SupervisorState,
    gateway: LiveGateway,
    model: str,
    *,
    read_only: bool = False,
    browser: bool = False,
) -> str:
    """A githi session whose composition pins this gateway and ``model``; when
    ``read_only``, on a read-only sandbox (a strict session); when ``browser``,
    on a sandbox profile that grants the browser model."""
    catalog = state.composition_catalog
    assert catalog is not None
    base = catalog.get(
        "composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1]
    )
    plug = dataclasses.replace(base.plug_bindings, gateway_base_url=gateway.url, model_route=model)
    changes: dict[str, Any] = {"plug_bindings": plug}
    if read_only or browser:
        profile = catalog.get(
            "sandbox_profile",
            "githi-docker-v1",
            catalog.list_versions("sandbox_profile", "githi-docker-v1")[-1],
        )
        strict = catalog.offer(
            dataclasses.replace(
                profile,
                name=f"strict-{uuid.uuid4().hex[:6]}",
                digest="",
                provider=SandboxProviderName.LOCAL_PROCESS,
                image_digest=f"sha256:{hashlib.sha256(b'test-image').hexdigest()}",
                file_effects=FileEffects.READ_ONLY if read_only else profile.file_effects,
                browser_model=browser,
            )
        )
        await persist_offer(env.factory, catalog, "sandbox_profile", strict.name, strict.digest)  # type: ignore[arg-type]
        changes["sandbox_profile"] = DigestRef(id=strict.name, digest=strict.digest)
    variant = catalog.offer(
        dataclasses.replace(base, name=f"githi-{uuid.uuid4().hex[:6]}", digest="", **changes)
    )
    await persist_offer(env.factory, catalog, "composition", variant.name, variant.digest)  # type: ignore[arg-type]
    sid = uuid.uuid4().hex
    await env.sessions.create(
        sid,
        CreateSession(
            "t1",
            "githi-v1",
            composition_id=variant.name,
            composition_digest=variant.digest,
            principal_id="alice",
        ),
        timing=FAST,
    )
    return sid


@pytest.mark.asyncio(loop_scope="session")
async def test_a_githi_session_replays_signed_thinking_across_its_steps(
    env: Env, router: ModelRouter, gateway: LiveGateway
) -> None:
    state = await env.state()
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_on(env, state, gateway, "gateway/claude")
        await env.sessions.submit(sid, Message("c1", '/tool echo {"text": "ping"}'))
        await env.wait_for(state, sid, run_ended())
        await env.sessions.submit(sid, Message("c2", "and again"))
        events = await env.wait_for(state, sid, run_ended(2))

    assistant = [e for e in payloads(events, MessageEnd) if e.role == "assistant"]
    assert [p["type"] for p in assistant[0].content] == ["reasoning", "tool_call"]
    reasoning = assistant[0].content[0]
    assert reasoning["api"] == "anthropic-messages" and reasoning["model"] == "gateway/claude"
    assert payloads(events, ToolEnd)[0].result["ok"] is True
    assert assistant[1].content[-1]["text"].startswith("echo:")  # the replayed signature verified
    assert assistant[2].content[-1] == {"type": "text", "text": "echo:and again"}
    assert all(m.model == "gateway/claude" for m in assistant)
    assert assistant[0].usage is not None and assistant[0].usage.cost_total > 0


# -- the browse runner's model endpoint --------------------------------------------

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_browser_endpoint_relays_and_converts_on_the_sessions_key(
    env: Env, router: ModelRouter, gateway: LiveGateway
) -> None:
    from supervisor.main import create_app

    state = await env.state()
    access = BrowserAccess(
        BrowserCapability(["capability-secret-" + "x" * 16]), "http://supervisor.internal:8080"
    )
    state.manager._browser = access
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_on(env, state, gateway, "gateway/mix", browser=True)
        await env.sessions.submit(sid, Message("c1", "hello"))
        await env.wait_for(state, sid, run_ended())
        granted = await state.manager.tool_env(sid)
        api = TestClient(create_app(state=state))

        def post(body: dict[str, Any], token: str | None = None) -> httpx.Response:
            headers = {"authorization": f"Bearer {token or granted['BROWSE_LAB_MODEL_TOKEN']}"}
            return api.post("/v1/browser-model/chat/completions", json=body, headers=headers)

        relayed = post({"model": "gateway/mix", "messages": [{"role": "user", "content": "look"}]})
        refused = [
            post({"model": "gateway/claude", "messages": []}),
            post({"model": "gateway/mix", "messages": [], "stream": True}),
            post({"model": "gateway/mix", "messages": [], "api_base": "https://evil.example"}),
        ]
        forged = post({"model": "gateway/mix", "messages": []}, token="a.b.c")

        native_sid = await create_on(env, state, gateway, "gpt-6-astra-ultrafast", browser=True)
        native_env = await state.manager.tool_env(native_sid)
        native = post(
            {
                "model": "gpt-6-astra-ultrafast",
                "messages": [
                    {"role": "system", "content": "browse"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": '{"done": true}'},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{PNG}"},
                            },
                        ],
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"schema": {"type": "object"}},
                },
                "max_tokens": 100,
            },
            token=native_env["BROWSE_LAB_MODEL_TOKEN"],
        )

    assert granted["BROWSE_LAB_BASE_URL"] == "http://supervisor.internal:8080/v1/browser-model"
    assert granted["BROWSE_LAB_MODEL"] == "gateway/mix"
    assert relayed.status_code == 200, relayed.text
    assert relayed.json()["choices"][0]["message"]["content"] == "echo:look"
    assert [r.status_code for r in refused] == [400, 400, 400]
    assert forged.status_code == 403
    assert native.status_code == 200, native.text
    assert native.json()["choices"][0]["message"]["content"] == '{"done":true}'
    assert native.json()["model"] == "gpt-6-astra-ultrafast"


@pytest.mark.asyncio(loop_scope="session")
async def test_commands_get_no_browser_capability_unless_the_profile_grants_it(
    env: Env, router: ModelRouter, gateway: LiveGateway
) -> None:
    """Every shell command could read it, so a prompt injection could send it away."""
    state = await env.state()
    capability = BrowserCapability(["capability-secret-" + "x" * 16])
    state.manager._browser = BrowserAccess(capability, "http://supervisor.internal:8080")
    async with session_worker(env.client, state, task_queue=env.queue):
        plain = await create_on(env, state, gateway, "gateway/mix")
        granted = await create_on(env, state, gateway, "gateway/mix", browser=True)
        assert await state.manager.tool_env(plain) == {}
        assert "BROWSE_LAB_MODEL_TOKEN" in await state.manager.tool_env(granted)


@pytest.mark.asyncio(loop_scope="session")
async def test_strict_sessions_get_no_browser_access(
    env: Env, router: ModelRouter, gateway: LiveGateway
) -> None:
    from supervisor.main import create_app

    state = await env.state()
    capability = BrowserCapability(["capability-secret-" + "x" * 16])
    state.manager._browser = BrowserAccess(capability, "http://supervisor.internal:8080")
    async with session_worker(env.client, state, task_queue=env.queue):
        sid = await create_on(env, state, gateway, "gateway/mix", read_only=True, browser=True)
        row = await state.manager._get_session_row(sid)
        assert state.manager.strict_posture(row)
        assert await state.manager.tool_env(sid) == {}
        token = capability.mint(BrowserGrant(sid, "t1", "alice", "gateway/mix"))
        refused = TestClient(create_app(state=state)).post(
            "/v1/browser-model/chat/completions",
            json={"model": "gateway/mix", "messages": [{"role": "user", "content": "x"}]},
            headers={"authorization": f"Bearer {token}"},
        )
    assert refused.status_code == 403
    assert "strict posture" in refused.json()["error"]
