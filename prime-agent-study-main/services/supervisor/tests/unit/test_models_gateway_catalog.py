"""Gateway configuration, discovery and routing (ported from QM's gateway-catalog and
provider-endpoints tests)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from supervisor.models.catalog import GatewayCatalog
from supervisor.models.client import ModelRouter, ModelUnavailable, UnsupportedAlias
from supervisor.models.endpoints import (
    GatewayConfig,
    GatewayConfigError,
    GatewayModelUnavailable,
    gateway_request,
    parse_provider_base_url,
)
from supervisor.models.registry import resolve_builtin
from supervisor.models.spec import WireApi

CONFIG = GatewayConfig(
    url="https://gateway.example/prefix/v1", api_key="private-key", api_key_header="x-gateway-key"
)
TICK = 300.001


def group(model_id: str = "vendor/new-model", **extra: Any) -> dict[str, Any]:
    return {
        "model_group": model_id,
        "mode": "chat",
        "supports_function_calling": True,
        "max_input_tokens": 128000,
        "max_output_tokens": 8192,
        "input_cost_per_token": 0.000002,
        "output_cost_per_token": 0.000008,
        "supports_vision": True,
        "supported_openai_params": ["tools", "max_completion_tokens"],
        **extra,
    }


@dataclass
class Fixture:
    metadata: list[dict[str, Any]]
    aliases: dict[str, str] = field(default_factory=dict)
    clock: float = 0.0
    status: int = 200
    listing: list[str] | None = None
    calls: list[httpx.Request] = field(default_factory=list)
    respond: Callable[[httpx.Request], httpx.Response] | None = None

    def __post_init__(self) -> None:
        if self.listing is None:
            self.listing = [m["model_group"] for m in self.metadata]
        self.catalog = GatewayCatalog(
            GatewayConfig(CONFIG.url, CONFIG.api_key, CONFIG.api_key_header, self.aliases),
            transport=httpx.MockTransport(self._handle),
            now=lambda: self.clock,
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.respond is not None:
            return self.respond(request)
        data = (
            [{"id": m} for m in self.listing or []]
            if request.url.path.endswith("/models")
            else self.metadata
        )
        return httpx.Response(self.status, json={"data": data})

    def tick(self) -> None:
        self.clock += TICK


def test_discovery_reads_both_endpoints_with_the_key_and_ignores_transport_metadata() -> None:
    f = Fixture(
        [group("vendor/new-model", api_base="https://evil.example", api_key="metadata-secret")]
    )
    f.catalog.refresh()
    f.catalog.refresh()
    assert [str(c.url) for c in f.calls] == [
        "https://gateway.example/prefix/v1/models",
        "https://gateway.example/prefix/model_group/info",
    ]
    assert all(c.headers["x-gateway-key"] == "private-key" for c in f.calls)
    spec = f.catalog.admitted.resolve("gateway/vendor/new-model")
    assert spec is not None
    assert spec.api is WireApi.OPENAI_COMPLETIONS
    assert spec.base_url == CONFIG.url
    assert (
        spec.pricing.input,
        spec.pricing.output,
        spec.pricing.cache_read,
        spec.pricing.cache_write,
    ) == (
        pytest.approx(2),
        pytest.approx(8),
        pytest.approx(2),
        pytest.approx(2),
    )
    request = gateway_request(f.catalog, spec)
    assert request is not None and request.target == "vendor/new-model"
    assert "evil.example" not in repr(spec) and "metadata-secret" not in repr(spec)


def test_only_models_in_both_responses_with_valid_chat_metadata_are_admitted() -> None:
    f = Fixture(
        [
            group(),
            group("embed", mode="embedding"),
            group("no-tools", supports_function_calling=False),
            group("no-context", max_input_tokens=None),
            group("bad-price", input_cost_per_token=-1),
            group("bad-output", max_output_tokens=200000),
            group("not-authorized"),
        ],
        listing=["vendor/new-model", "embed", "no-tools", "no-context", "bad-price", "bad-output"],
    )
    f.catalog.refresh()
    assert list(f.catalog.models) == ["gateway/vendor/new-model", "gateway/embed"]
    assert [m.id for m in f.catalog.admitted.chat_models()] == ["gateway/vendor/new-model"]
    assert f.catalog.admitted.embedding_ids() == ["gateway/embed"]


def test_a_successful_revocation_clears_aliases_and_a_later_failure_cannot_undo_it() -> None:
    f = Fixture([group()], aliases={"claude-opus-5": "vendor/new-model"})
    f.catalog.refresh()
    stale = f.catalog.admitted.resolve("gateway/vendor/new-model")
    native = resolve_builtin("claude-opus-5")
    assert stale is not None and native is not None
    assert f.catalog.models["claude-opus-5"] == "vendor/new-model"
    f.tick()
    f.listing = []
    f.catalog.refresh()
    assert dict(f.catalog.models) == {}
    assert f.catalog.admitted.resolve(stale.id) is None
    with pytest.raises(GatewayModelUnavailable, match="unavailable"):
        gateway_request(f.catalog, stale)
    with pytest.raises(GatewayModelUnavailable):
        gateway_request(f.catalog, native)
    f.tick()
    f.status = 503
    f.catalog.refresh()
    assert dict(f.catalog.models) == {}
    with pytest.raises(GatewayModelUnavailable):
        gateway_request(None, stale)


def test_aliases_work_before_discovery_then_discovery_governs_access() -> None:
    f = Fixture([group()], aliases={"claude-opus-5": "vendor/new-model"})
    f.status = 404
    f.catalog.refresh()
    assert f.catalog.models["claude-opus-5"] == "vendor/new-model"
    f.tick()
    f.status = 200
    f.catalog.refresh()
    assert "gateway/vendor/new-model" in f.catalog.models
    f.tick()
    f.status = 401
    f.catalog.refresh()
    assert dict(f.catalog.models) == {}


def test_a_transient_blip_keeps_routes_and_a_prolonged_outage_hides_them() -> None:
    f = Fixture([group()], aliases={"claude-opus-5": "vendor/new-model"})
    f.catalog.refresh()
    native = resolve_builtin("claude-opus-5")
    assert native is not None
    f.tick()
    f.status = 503
    f.catalog.refresh()
    assert f.catalog.admitted.resolve("gateway/vendor/new-model") is not None
    assert gateway_request(f.catalog, native) is not None
    for _ in range(3):
        f.tick()
        f.catalog.refresh()
    assert f.catalog.admitted.resolve("gateway/vendor/new-model") is None
    with pytest.raises(GatewayModelUnavailable):
        gateway_request(f.catalog, native)
    f.tick()
    f.status = 200
    f.metadata = [group("new")]
    f.listing = ["new"]
    f.catalog.refresh()
    assert f.catalog.admitted.resolve("gateway/new") is not None


def test_failures_wait_thirty_seconds_and_successes_five_minutes() -> None:
    f = Fixture([group()])
    f.status = 500
    f.catalog.refresh()
    f.catalog.refresh()
    assert len(f.calls) == 1  # the first read failed, so the second endpoint was never asked
    f.clock += 30
    f.status = 200
    f.catalog.refresh()
    assert len(f.calls) == 3
    f.clock += 299
    f.catalog.refresh()
    assert len(f.calls) == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"nope": []}),
        httpx.Response(200, json={"data": [{"id": "m"}] * 1001}),
        httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)),
        httpx.Response(200, content=b"{}", headers={"content-length": "99999999"}),
        httpx.Response(200, json={"data": [{"id": "bad id!"}]}),
        httpx.Response(302, headers={"location": "https://elsewhere.example"}),
    ],
)
def test_malformed_oversized_or_redirected_catalogs_grant_nothing(response: httpx.Response) -> None:
    f = Fixture([group()], respond=lambda _request: response)
    f.catalog.refresh()
    assert dict(f.catalog.models) == {}


def test_wire_protocol_and_documents_follow_provider_labels() -> None:
    f = Fixture(
        [
            group("chat-model"),
            group(
                "anthropic-model",
                providers=["anthropic"],
                supports_reasoning=True,
                supports_adaptive_thinking=True,
            ),
            group(
                "openai-model",
                providers=["openai", "azure"],
                supports_reasoning=True,
                supported_openai_params=["reasoning_effort"],
            ),
            group("google-docs", providers=["gemini"], supports_reasoning=True),
            group("mixed-docs", providers=["openai", "unknown"]),
        ]
    )
    f.catalog.refresh()
    get = f.catalog.admitted.resolve
    anthropic, openai, google, mixed, chat = (
        get("gateway/anthropic-model"),
        get("gateway/openai-model"),
        get("gateway/google-docs"),
        get("gateway/mixed-docs"),
        get("gateway/chat-model"),
    )
    assert anthropic and openai and google and mixed and chat
    assert anthropic.api is WireApi.ANTHROPIC_MESSAGES
    assert anthropic.base_url == "https://gateway.example/prefix"
    assert anthropic.reasoning and anthropic.compat.force_adaptive_thinking
    assert openai.api is WireApi.OPENAI_RESPONSES and openai.document_input == "files"
    assert (
        openai.compat.supports_reasoning_effort and openai.compat.max_tokens_field == "max_tokens"
    )
    assert google.api is WireApi.OPENAI_COMPLETIONS and google.document_input == "pdf"
    assert not google.reasoning  # reasoning controls only for native Anthropic and OpenAI groups
    assert mixed.document_input is None
    assert chat.compat.max_tokens_field == "max_completion_tokens"
    assert {s.compat.send_session_affinity_headers for s in (anthropic, openai, chat)} == {True}


def test_gateway_requests_point_each_protocol_at_its_base_path() -> None:
    f = Fixture([group("a", providers=["anthropic"]), group("o", providers=["openai"]), group("c")])
    f.catalog.refresh()
    bases = {
        model_id: gateway_request(f.catalog, f.catalog.admitted.resolve(model_id)).spec.base_url  # type: ignore[arg-type,union-attr]
        for model_id in ("gateway/a", "gateway/o", "gateway/c")
    }
    assert bases == {
        "gateway/a": "https://gateway.example/prefix",
        "gateway/o": "https://gateway.example/prefix/v1",
        "gateway/c": "https://gateway.example/prefix/v1",
    }


def test_native_aliases_route_through_the_configured_url_with_affinity() -> None:
    config = GatewayConfig(
        "http://gateway.internal/v1",
        "k",
        "x-gateway-key",
        {"claude-opus-5-5": "router/claude-opus-5-5", "gpt-5.6-sol": "router/gpt-5.6-sol"},
    )
    catalog = GatewayCatalog(config, transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
    catalog.refresh()
    for model_id in config.aliases:
        spec = resolve_builtin(model_id)
        assert spec is not None
        routed = gateway_request(catalog, spec, api_key="vk_session")
        assert routed is not None
        assert routed.spec.compat.send_session_affinity_headers is True
        assert routed.spec.base_url == config.url
        assert routed.spec.id == model_id
        assert routed.headers == {"x-gateway-key": "vk_session"}
    assert gateway_request(catalog, resolve_builtin("claude-sonnet-5")) is None  # type: ignore[arg-type]


def test_picker_aliases_hide_the_duplicate_discovered_group() -> None:
    target = "anthropic/claude-opus-5"
    f = Fixture([group(target)], aliases={"claude-opus-5": target})
    f.catalog.refresh()
    assert f"gateway/{target}" not in [m.id for m in f.catalog.admitted.chat_models()]
    assert f"gateway/{target}" in [
        m.id for m in f.catalog.admitted.chat_models(include_aliased=True)
    ]
    for model_id in ("claude-opus-5", f"gateway/{target}"):
        spec = resolve_builtin(model_id) or f.catalog.admitted.resolve(model_id)
        assert spec is not None
        assert gateway_request(f.catalog, spec).target == target  # type: ignore[union-attr]


def test_aliases_outside_the_picker_do_not_hide_discovered_models() -> None:
    f = Fixture([group("anthropic/opus-4-7")], aliases={"claude-opus-4-7": "anthropic/opus-4-7"})
    f.catalog.refresh()
    assert "gateway/anthropic/opus-4-7" in [m.id for m in f.catalog.admitted.chat_models()]


def test_configuration_from_environment() -> None:
    env = {
        "MODEL_GATEWAY_URL": "http://gateway.internal:8080/",
        "MODEL_GATEWAY_API_KEY": "secret",
        "MODEL_GATEWAY_API_KEY_HEADER": "api-key",
        "MODEL_GATEWAY_MODELS": "claude-opus-5=router/opus,claude-haiku-4-5=router/haiku",
    }
    assert GatewayConfig.from_env(env) == GatewayConfig(
        "http://gateway.internal:8080",
        "secret",
        "api-key",
        {"claude-opus-5": "router/opus", "claude-haiku-4-5": "router/haiku"},
    )
    assert GatewayConfig.from_env({}) is None
    assert GatewayConfig.from_env({**env, "MODEL_GATEWAY_MODELS": ""}).aliases == {}  # type: ignore[union-attr]
    with pytest.raises(GatewayConfigError, match="required"):
        GatewayConfig.from_env({"MODEL_GATEWAY_URL": "http://gateway.internal"})
    with pytest.raises(GatewayConfigError, match="HTTP header name"):
        GatewayConfig.from_env({**env, "MODEL_GATEWAY_API_KEY_HEADER": "bad header"})
    for bad in ("claude-opus-5", "=x", "a=", "claude-opus-5=a,claude-opus-5=b"):
        with pytest.raises(GatewayConfigError, match="MODEL_GATEWAY_MODELS"):
            GatewayConfig.from_env({**env, "MODEL_GATEWAY_MODELS": bad})


def test_base_urls_are_normalized_or_rejected() -> None:
    assert (
        parse_provider_base_url("X", " https://gw.example.com/v1// ") == "https://gw.example.com/v1"
    )
    for bad in (
        "not a url",
        "ftp://gw.example.com",
        "https://user:pw@gw.example.com",
        "https://gw.example.com/?a=b",
        "https://gw.example.com/#frag",
    ):
        with pytest.raises(GatewayConfigError):
            parse_provider_base_url("X", bad)


def test_the_router_rejects_aliases_the_registry_does_not_define() -> None:
    with pytest.raises(UnsupportedAlias, match="not-a-model"):
        ModelRouter(GatewayConfig(CONFIG.url, "k", "h", {"not-a-model": "x"}))


def test_the_router_resolves_gateway_native_and_legacy_ids() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "githi"}, {"id": "opus"}]})
        return httpx.Response(
            200, json={"data": [group("githi"), group("opus", providers=["anthropic"])]}
        )

    router = ModelRouter(
        GatewayConfig(CONFIG.url, "k", "h", {"claude-opus-5": "opus"}),
        transport=httpx.MockTransport(respond),
    )
    router.refresh()
    assert router.resolve("gateway/githi").id == "gateway/githi"
    assert router.resolve("githi").id == "gateway/githi"
    assert router.resolve("claude-opus-5").api is WireApi.ANTHROPIC_MESSAGES
    assert router.target_for("claude-opus-5") == "opus"
    assert router.target_for("githi") == "githi"
    with pytest.raises(GatewayModelUnavailable):
        router.resolve("gateway/removed")
    with pytest.raises(ModelUnavailable):
        router.resolve("nonsense")
    with pytest.raises(ModelUnavailable, match="MODEL_GATEWAY_MODELS"):
        router.route(router.resolve("claude-sonnet-5"))
    assert [s.id for s in router.selectable_models()] == ["gateway/githi", "claude-opus-5"]
