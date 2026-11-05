"""The model vendors the gateway can call: where each is reached, and with which key.

A vendor is an upstream API the gateway holds a credential for. Most speak OpenAI chat
completions at their own base URL, so one provider implementation serves them all; what
differs is the base URL, the key's environment variable, and whether a key is needed.
QM keeps the same split (``src/model/provider-endpoints.ts``: one place decides each
provider's base URL; ``src/model/custom-providers.ts``: any upstream speaking a wire
protocol already run is a base URL, a key and model ids).

The presets and their endpoints, as each vendor documents them:

- ``deepseek``: ``https://api.deepseek.com``, ``DEEPSEEK_API_KEY`` (api-docs.deepseek.com;
  pi-ai ``catalog/providers/deepseek.ts``);
- ``zai`` (GLM): the general API ``https://api.z.ai/api/paas/v4``, ``ZAI_API_KEY`` (pi-ai
  ``catalog/providers/zai.ts`` for the key's name; the coding-plan path it uses is a
  subscription, not the metered API);
- ``qwen`` (Alibaba Cloud Model Studio, international):
  ``https://dashscope-intl.aliyuncs.com/compatible-mode/v1``, ``DASHSCOPE_API_KEY``;
- ``local``: a server on the operator's machine speaking the OpenAI API (Ollama at
  ``http://localhost:11434/v1``, vLLM, LM Studio). It needs no key (Ollama ignores one),
  so it is enabled by setting ``LOCAL_MODEL_BASE_URL``, which a deployment does only when
  that server is meant to be reached.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

__all__ = ["VENDORS", "Vendor", "parse_base_url"]


@dataclass(frozen=True, slots=True)
class Vendor:
    id: str
    """The provider name routes use (``Route.provider``)."""
    label: str
    api: Literal["openai", "anthropic"]
    """The wire family: OpenAI-compatible (``base_url`` ends in its ``/v1``-style prefix) or
    Anthropic (``base_url`` is the origin, as ``ANTHROPIC_BASE_URL`` is everywhere)."""
    base_url: str
    """The default endpoint; empty when there is none (a local server)."""
    key_env: str
    base_url_env: str
    key_required: bool = True
    responses: bool = False
    """Also serves OpenAI Responses (only OpenAI itself)."""
    key_check: str | None = "/models"
    """The authenticated GET (under ``base_url``) that tells whether a key is accepted, as
    QM checks a key before saving it (``src/api/routes/admin/model-providers.ts``
    ``VALIDATION_REQUESTS``; ``custom-providers.ts`` uses ``/models`` for any OpenAI-compatible
    upstream). ``None`` when the vendor documents no such call: its key is saved unchecked."""


VENDORS: Mapping[str, Vendor] = {
    vendor.id: vendor
    for vendor in (
        Vendor("openai", "OpenAI", "openai", "https://api.openai.com/v1",
               "OPENAI_API_KEY", "OPENAI_BASE_URL", responses=True),
        Vendor("anthropic", "Anthropic", "anthropic", "https://api.anthropic.com",
               "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", key_check="/v1/models"),
        Vendor("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
               "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", key_check="/key"),
        Vendor("deepseek", "DeepSeek", "openai", "https://api.deepseek.com",
               "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"),
        Vendor("zai", "Z.ai (GLM)", "openai", "https://api.z.ai/api/paas/v4",
               "ZAI_API_KEY", "ZAI_BASE_URL", key_check=None),
        Vendor("qwen", "Qwen (Alibaba Cloud)", "openai",
               "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
               "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", key_check=None),
        Vendor("local", "Local", "openai", "",
               "LOCAL_MODEL_API_KEY", "LOCAL_MODEL_BASE_URL", key_required=False),
    )
}  # fmt: skip
"""Every vendor the gateway registers a provider for, by provider name."""


def parse_base_url(env_name: str, value: str) -> str:
    """A vendor base URL, normalized (no trailing slash); empty stays empty.

    Rejects what would silently misroute a request or leak a credential, as QM's
    ``parseProviderBaseUrl`` does: a scheme other than http(s), credentials in the URL,
    a query string or a fragment.
    """
    trimmed = value.strip().rstrip("/")
    if not trimmed:
        return ""
    try:
        parts = urlsplit(trimmed)
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError:
        raise ValueError(f"{env_name} is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{env_name} must be an http(s) URL")
    if parts.username is not None or parts.password is not None:
        raise ValueError(f"{env_name} must not contain credentials")
    if parts.query or trimmed.endswith("?"):
        raise ValueError(f"{env_name} must not contain a query string")
    if parts.fragment or "#" in trimmed:
        raise ValueError(f"{env_name} must not contain a fragment")
    return trimmed
