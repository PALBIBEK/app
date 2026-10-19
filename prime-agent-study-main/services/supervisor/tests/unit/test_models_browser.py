"""The browse runner's model access: its capability, request checks and conversions
(ported from QM's browser-model and browser-completion behaviour)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest

from supervisor.models.browser import (
    BROWSER_MODEL_AUD,
    BrowserAccess,
    BrowserCapability,
    BrowserCompletionError,
    BrowserGrant,
    GatewayLeg,
    direct_leg,
    native_completion,
    relay,
    validate_request,
)
from supervisor.models.client import ModelRouter, RoutedModelClient
from supervisor.models.endpoints import GatewayConfig
from supervisor.sandbox.security.env import sanitize_sandbox_env

SECRET_A, OLD, NEW, OTHER = ("a" * 32, "o" * 32, "n" * 32, "x" * 32)
GRANT = BrowserGrant(
    session_id="s-1", tenant_id="t", actor_id="alice", model="gpt-6-astra-ultrafast"
)


def test_a_capability_round_trips_and_names_only_its_grant() -> None:
    capability = BrowserCapability([SECRET_A])
    token = capability.mint(GRANT)
    grant = capability.verify(token)
    assert grant is not None
    assert (grant.session_id, grant.tenant_id, grant.actor_id, grant.model, grant.account) == (
        "s-1",
        "t",
        "alice",
        "gpt-6-astra-ultrafast",
        "company",
    )
    assert grant.expires_at == pytest.approx(time.time() + 3600, abs=5)
    claims = jwt.decode(token, SECRET_A, algorithms=["HS256"], audience=BROWSER_MODEL_AUD)
    assert "key" not in json.dumps(claims).lower()


def test_capabilities_fail_closed() -> None:
    capability = BrowserCapability([SECRET_A])
    expired = capability.mint(GRANT, now=time.time() - 3601)
    other_audience = jwt.encode(
        {
            "aud": "control-plane",
            "sub": "a",
            "sid": "s",
            "tid": "t",
            "browser_model": "m",
            "exp": int(time.time()) + 60,
        },
        SECRET_A,
        algorithm="HS256",
    )
    personal = jwt.encode(
        {
            "aud": BROWSER_MODEL_AUD,
            "sub": "a",
            "sid": "s",
            "tid": "t",
            "browser_model": "m",
            "browser_account": "personal",
            "exp": int(time.time()) + 60,
        },
        SECRET_A,
        algorithm="HS256",
    )
    for token in (
        expired,
        other_audience,
        personal,
        "not-a-token",
        BrowserCapability([OTHER]).mint(GRANT),
    ):
        assert capability.verify(token) is None


def test_the_signing_secret_can_rotate() -> None:
    old = BrowserCapability([OLD]).mint(GRANT)
    rotated = BrowserCapability([NEW, OLD])
    assert rotated.verify(old) is not None
    assert rotated.verify(rotated.mint(GRANT)) is not None


def test_access_is_configured_from_the_environment() -> None:
    assert BrowserAccess.from_env({}) is None
    assert BrowserAccess.from_env({"SUPERVISOR_PUBLIC_URL": "http://sup:8080"}) is None
    access = BrowserAccess.from_env(
        {"SUPERVISOR_PUBLIC_URL": "http://sup:8080/", "CAPABILITY_SECRET": SECRET_A}
    )
    assert access is not None
    env = access.env(GRANT)
    assert env["BROWSE_LAB_BASE_URL"] == "http://sup:8080/v1/browser-model"
    assert env["BROWSE_LAB_MODEL"] == GRANT.model
    assert env["BROWSE_LAB_MODEL_PROVIDER"] == "managed"
    assert access.capability.verify(env["BROWSE_LAB_MODEL_TOKEN"]) is not None


def test_only_the_capability_survives_sandbox_env_scrubbing() -> None:
    access = BrowserAccess(BrowserCapability([SECRET_A]), "http://sup:8080")
    env = sanitize_sandbox_env({**access.env(GRANT), "OTHER_TOKEN": "x"}, base={})
    assert set(env) == {
        "BROWSE_LAB_MODEL_PROVIDER",
        "BROWSE_LAB_MODEL",
        "BROWSE_LAB_BASE_URL",
        "BROWSE_LAB_MODEL_TOKEN",
    }
    assert "BROWSE_LAB_MODEL_TOKEN" not in sanitize_sandbox_env(
        {"BROWSE_LAB_MODEL_TOKEN": "sk-live"}, base={}
    )


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"model": OTHER, "messages": []},
        {"model": GRANT.model, "messages": "hi"},
        {"model": GRANT.model, "messages": [], "stream": True},
        {"model": GRANT.model, "messages": [], "api_base": "https://evil.example"},
    ],
)
def test_requests_outside_the_contract_are_refused(body: Any) -> None:
    with pytest.raises(BrowserCompletionError) as caught:
        validate_request(body, GRANT)
    assert caught.value.status == 400


def test_relayed_failures_map_to_429_or_502() -> None:
    leg = GatewayLeg("https://gw.example/v1/chat/completions", {"x-key": "k"}, "group")
    for status, expected in ((429, 429), (500, 502), (401, 502)):
        http = httpx.Client(transport=httpx.MockTransport(lambda _r, s=status: httpx.Response(s)))
        with pytest.raises(BrowserCompletionError) as caught:
            relay(leg, {"messages": []}, http)
        assert caught.value.status == expected
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": []})

    assert relay(
        leg,
        {"model": "m", "messages": [], "stream": True},
        httpx.Client(transport=httpx.MockTransport(ok)),
    )
    assert json.loads(seen[0].content) == {"model": "group", "messages": [], "stream": False}
    assert seen[0].headers["x-key"] == "k"


def test_without_a_session_key_the_direct_gateway_is_unavailable() -> None:
    with pytest.raises(BrowserCompletionError) as caught:
        direct_leg("http://gw:8090", model="githi", api_key=None)
    assert caught.value.status == 503
    assert (
        direct_leg("http://gw:8090/v1/", model="githi", api_key="vk_1").url
        == "http://gw:8090/v1/chat/completions"
    )


def _client() -> RoutedModelClient:
    router = ModelRouter(
        GatewayConfig("https://gw.example/v1", "k", "h", {"gpt-6-astra-ultrafast": "astra"}),
        transport=httpx.MockTransport(lambda _r: httpx.Response(404)),
    )
    return RoutedModelClient(router)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"tools": [{}], "messages": []}, "Unsupported browser completion request"),
        ({"messages": [{"role": "tool", "content": "x"}]}, "Unsupported browser message"),
        ({"messages": [{"role": "user", "content": 5}]}, "Invalid browser message content"),
        (
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
                    }
                ]
            },
            "inline image data",
        ),
        (
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": "data:image/png;base64,AA=="},
                            }
                        ],
                    }
                ]
            },
            "Unsupported browser message content",
        ),
        ({"messages": [], "response_format": {"type": "json_object"}}, "require a JSON schema"),
        ({"messages": [], "max_tokens": 0}, "Invalid browser output token limit"),
    ],
)
def test_native_completions_refuse_what_they_cannot_convert(
    body: dict[str, Any], message: str
) -> None:
    with pytest.raises(BrowserCompletionError, match=message):
        native_completion(_client(), model="gpt-6-astra-ultrafast", body=body)


def test_a_model_the_gateway_does_not_route_is_unavailable() -> None:
    with pytest.raises(BrowserCompletionError) as caught:
        native_completion(_client(), model="no-such-model", body={"messages": []})
    assert caught.value.status == 409
