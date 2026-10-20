"""Model access for the sandboxed browser agent, without handing it a model key.

A session's turn may carry a one-hour *browser capability*: a signed token bound
to the session, its principal and the model it had selected. The browse runner in
the sandbox sends OpenAI-compatible, non-streaming chat completions to
``POST /v1/browser-model/chat/completions`` with it; the supervisor re-checks the
selection on every request and makes the call itself, through the same gateway
key (and so the same permissions and budget) as the session's own model calls.

* A model whose requests need a native protocol (an Ultrafast selection) is called
  through the routed transports, with screenshots and structured output
  converted for it.
* Every other model is relayed to the gateway's chat completions as is.

Strict sessions (a read-only sandbox) get no capability and are refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import jwt

from supervisor.models.client import ModelRouter, ModelUnavailable, RoutedModelClient
from supervisor.models.endpoints import GATEWAY_MODEL_PREFIX, GatewayModelUnavailable
from supervisor.models.messages import (
    AssistantMessage,
    Context,
    Image,
    Message,
    TextBlock,
    Tool,
    ToolCallBlock,
    UserMessage,
)
from supervisor.models.registry import request_override
from supervisor.models.spec import WireApi
from supervisor.models.transports import ModelCallError

BROWSER_MODEL_AUD = "browser-model"
CAPABILITY_TTL_S = 60 * 60
MAX_BODY_BYTES = 16 * 1024 * 1024
UPSTREAM_TIMEOUT_S = 120.0
BROWSER_RESULT_TOOL = "browser_result"
DEFAULT_OUTPUT_TOKENS = 4096

REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "max_completion_tokens",
        "temperature",
        "top_p",
        "seed",
        "stop",
        "response_format",
        "reasoning_effort",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "verbosity",
        "stream",
    }
)
"""The only fields a browser request may carry; anything else (a provider override) is refused."""

ACCOUNT_CHANGED = "AI access changed or is unavailable; start a new turn to refresh browser access"
COULD_NOT_COMPLETE = "Selected AI account could not complete the browser request"
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$")

type Account = Literal["company"]


class BrowserCompletionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# -- the capability --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrowserGrant:
    session_id: str
    tenant_id: str
    actor_id: str
    model: str
    account: Account = "company"
    expires_at: float = 0.0


def signing_key_id(secret: str) -> str:
    digest = hmac.new(secret.encode(), b"yal-signing-key-id", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")[:8]


class BrowserCapability:
    """Mints and verifies browser capabilities (HS256, ``aud`` ``browser-model``).

    Verification accepts any of ``secrets`` so the signing secret can rotate; the
    first one signs.
    """

    def __init__(self, secrets: Sequence[str]) -> None:
        if not secrets or not all(secrets):
            raise ValueError("a browser capability needs a signing secret")
        self._secrets = tuple(secrets)

    def mint(self, grant: BrowserGrant, *, now: float | None = None) -> str:
        issued = time.time() if now is None else now
        claims = {
            "aud": BROWSER_MODEL_AUD,
            "sub": grant.actor_id,
            "sid": grant.session_id,
            "tid": grant.tenant_id,
            "browser_model": grant.model,
            "browser_account": grant.account,
            "exp": int(issued + CAPABILITY_TTL_S),
        }
        secret = self._secrets[0]
        return jwt.encode(
            claims, secret, algorithm="HS256", headers={"kid": signing_key_id(secret)}
        )

    def verify(self, token: str) -> BrowserGrant | None:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
        except jwt.PyJWTError:
            return None
        ordered = sorted(self._secrets, key=lambda s: signing_key_id(s) != kid)
        for secret in ordered:
            try:
                claims = jwt.decode(token, secret, algorithms=["HS256"], audience=BROWSER_MODEL_AUD)
            except jwt.ExpiredSignatureError:
                return None
            except jwt.PyJWTError:
                continue
            fields = ("sub", "sid", "tid", "browser_model")
            if not all(isinstance(claims.get(f), str) and claims[f] for f in fields):
                return None
            if claims.get("browser_account", "company") != "company":
                return None
            return BrowserGrant(
                session_id=claims["sid"],
                tenant_id=claims["tid"],
                actor_id=claims["sub"],
                model=claims["browser_model"],
                expires_at=float(claims["exp"]),
            )
        return None


@dataclass(frozen=True, slots=True)
class BrowserAccess:
    """Where the sandbox reaches the browser endpoint, and what signs its capability."""

    capability: BrowserCapability
    base_url: str
    """The supervisor's URL as the sandbox sees it."""

    def env(self, grant: BrowserGrant) -> dict[str, str]:
        """The browse runner's environment for one turn."""
        return {
            "BROWSE_LAB_MODEL_PROVIDER": "managed",
            "BROWSE_LAB_MODEL": grant.model,
            "BROWSE_LAB_BASE_URL": f"{self.base_url.rstrip('/')}/v1/browser-model",
            "BROWSE_LAB_MODEL_TOKEN": self.capability.mint(grant),
        }

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> BrowserAccess | None:
        """Configured when the supervisor knows its sandbox-facing URL and a signing secret."""
        base_url = env.get("SUPERVISOR_PUBLIC_URL", "").strip()
        raw = env.get("CAPABILITY_SECRET", "").strip() or env.get("SERVICE_JWT_SECRET", "").strip()
        secrets = [s.strip() for s in raw.split(",") if s.strip()]
        if not base_url or not secrets:
            return None
        return cls(BrowserCapability(secrets), base_url)


# -- requests ----------------------------------------------------------------------


def validate_request(body: object, grant: BrowserGrant) -> dict[str, Any]:
    """The request if it is a non-streaming completion for the granted model, else 400."""
    if (
        not isinstance(body, dict)
        or body.get("model") != grant.model
        or not isinstance(body.get("messages"), list)
        or body.get("stream") is True
        or any(key not in REQUEST_FIELDS for key in body)
    ):
        raise BrowserCompletionError("invalid browser model request", 400)
    return body


def needs_native_protocol(model: str) -> bool:
    """A selection that overrides another model's request needs its native protocol."""
    return request_override(model) is not None


@dataclass(frozen=True, slots=True)
class GatewayLeg:
    """Where a browser completion is relayed: the gateway's chat completions for one group."""

    url: str
    headers: Mapping[str, str]
    target: str


def routed_leg(router: ModelRouter, *, model: str, api_key: str | None) -> GatewayLeg:
    """The routed gateway's leg for ``model`` (a discovered group, an alias, a legacy route id)."""
    router.refresh()
    routes = router.catalog.models
    target = routes.get(model) or routes.get(f"{GATEWAY_MODEL_PREFIX}{model}")
    if target is None:
        raise BrowserCompletionError("browser model unavailable", 503)
    config = router.config
    return GatewayLeg(
        url=f"{config.root}/v1/chat/completions",
        headers={config.api_key_header: api_key or config.api_key},
        target=target,
    )


def direct_leg(base_url: str, *, model: str, api_key: str | None) -> GatewayLeg:
    """The session's chat-completions gateway (``GATEWAY_BASE_URL``), where routes are model ids."""
    if not api_key:
        raise BrowserCompletionError("Company model credentials are unavailable", 503)
    return GatewayLeg(
        url=f"{base_url.rstrip('/').removesuffix('/v1')}/v1/chat/completions",
        headers={"authorization": f"Bearer {api_key}"},
        target=model,
    )


def relay(leg: GatewayLeg, body: Mapping[str, Any], http: httpx.Client) -> Any:
    """Send the completion as is (model renamed, never streamed); a refusal is 429 or 502."""
    try:
        response = http.post(
            leg.url,
            content=json.dumps({**body, "model": leg.target, "stream": False}),
            headers={"content-type": "application/json", **leg.headers},
            timeout=UPSTREAM_TIMEOUT_S,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise BrowserCompletionError(COULD_NOT_COMPLETE, 502) from exc
    if not response.is_success:
        raise BrowserCompletionError(
            COULD_NOT_COMPLETE, 429 if response.status_code == 429 else 502
        )
    try:
        return response.json()
    except ValueError as exc:
        raise BrowserCompletionError(COULD_NOT_COMPLETE, 502) from exc


def native_completion(
    client: RoutedModelClient, *, model: str, body: Mapping[str, Any]
) -> dict[str, Any]:
    """Run the completion through the model's own protocol (screenshots and JSON-schema
    output converted), answering in chat-completion shape."""
    try:
        spec = client.spec(model)
    except (ModelUnavailable, GatewayModelUnavailable):
        raise BrowserCompletionError("Selected browser model is unavailable", 409) from None
    if body.get("tools") or body.get("tool_choice") or body.get("parallel_tool_calls"):
        raise BrowserCompletionError("Unsupported browser completion request")
    messages: list[Message] = []
    systems: list[str] = []
    for raw in body.get("messages") or []:
        role = raw.get("role") if isinstance(raw, dict) else None
        if role not in ("system", "developer", "user", "assistant"):
            raise BrowserCompletionError("Unsupported browser message")
        texts, images = _content(raw.get("content"), role=str(role))
        text = "\n".join(texts)
        if role in ("system", "developer"):
            systems.append(text)
        elif role == "user":
            messages.append(UserMessage(text, tuple(images)))
        else:
            messages.append(AssistantMessage((TextBlock(text),), model=spec.id))
    response_format = body.get("response_format")
    schema = None
    if response_format is not None:
        json_schema = (
            response_format.get("json_schema") if isinstance(response_format, dict) else None
        )
        schema = json_schema.get("schema") if isinstance(json_schema, dict) else None
        if (
            not isinstance(response_format, dict)
            or response_format.get("type") != "json_schema"
            or not isinstance(schema, dict)
        ):
            raise BrowserCompletionError("Browser responses require a JSON schema")
    tools: tuple[Tool, ...] = ()
    if schema is not None:
        tools = (
            Tool(BROWSER_RESULT_TOOL, "Return the browser agent's structured result.", schema),
        )
        systems.append(f"Return your result by calling {BROWSER_RESULT_TOOL} exactly once.")
    limit = body.get("max_completion_tokens", body.get("max_tokens", DEFAULT_OUTPUT_TOKENS))
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise BrowserCompletionError("Invalid browser output token limit")
    try:
        turn = client.call(
            spec,
            Context("\n\n".join(systems), tuple(messages), tools),
            max_tokens=min(limit, spec.max_tokens),
            tool_choice=_tool_choice(spec.api) if schema is not None else None,
            max_retries=0,
        )
    except ModelUnavailable:
        raise BrowserCompletionError("Company model credentials are unavailable", 503) from None
    except (ModelCallError, GatewayModelUnavailable):
        raise BrowserCompletionError(COULD_NOT_COMPLETE, 502) from None
    if turn.stop_reason == "length":
        raise BrowserCompletionError(COULD_NOT_COMPLETE, 502)
    structured = next(
        (b for b in turn.blocks if isinstance(b, ToolCallBlock) and b.name == BROWSER_RESULT_TOOL),
        None,
    )
    if schema is not None and structured is None:
        raise BrowserCompletionError(
            "Selected AI account did not return a structured browser result", 502
        )
    text = (
        json.dumps(dict(structured.arguments), separators=(",", ":"))
        if structured is not None
        else "".join(b.text for b in turn.blocks if isinstance(b, TextBlock))
    )
    return {
        "id": "browser-completion",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": turn.usage.input,
            "completion_tokens": turn.usage.output,
            "total_tokens": turn.usage.total_tokens,
        },
    }


def _content(content: object, *, role: str) -> tuple[list[str], list[Image]]:
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
    if not isinstance(parts, list):
        raise BrowserCompletionError("Invalid browser message content")
    texts: list[str] = []
    images: list[Image] = []
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            texts.append(part["text"])
        elif isinstance(part, dict) and part.get("type") == "image_url" and role == "user":
            url = (
                part.get("image_url", {}).get("url")
                if isinstance(part.get("image_url"), dict)
                else None
            )
            match = _DATA_URL.match(url) if isinstance(url, str) else None
            if match is None:
                raise BrowserCompletionError("Browser screenshots must be inline image data")
            images.append(Image(match.group(1), match.group(2)))
        else:
            raise BrowserCompletionError("Unsupported browser message content")
    return texts, images


def _tool_choice(api: WireApi) -> dict[str, Any]:
    if api is WireApi.ANTHROPIC_MESSAGES:
        return {"type": "tool", "name": BROWSER_RESULT_TOOL}
    if api is WireApi.OPENAI_COMPLETIONS:
        return {"type": "function", "function": {"name": BROWSER_RESULT_TOOL}}
    return {"type": "function", "name": BROWSER_RESULT_TOOL}


def complete_for_session(grant: BrowserGrant, client: object, body: dict[str, Any]) -> Any:
    """Answer a checked browser request with the session's own model client.

    Through the routed gateway, a model needing its native protocol runs on the
    transports and anything else is relayed to its group; a session on the
    chat-completions gateway is relayed there. Either way, on the session's key.
    """
    from supervisor.models.service import model_router
    from supervisor.seams.providers.gateway.http_gateway import HttpModelGatewayClient

    router = model_router()
    if router is not None and isinstance(client, RoutedModelClient):
        if needs_native_protocol(grant.model):
            return native_completion(client, model=grant.model, body=body)
        leg = routed_leg(router, model=grant.model, api_key=client.api_key)
    elif isinstance(client, HttpModelGatewayClient):
        leg = direct_leg(client.base_url, model=grant.model, api_key=client.token)
    else:
        raise BrowserCompletionError("Company model credentials are unavailable", 503)
    with httpx.Client() as http:
        return relay(leg, body, http)
