"""Sandbox environment sanitization — defense-in-depth secret filtering.

This module ensures sandbox processes never receive sensitive environment
variables from the host or control plane. It implements a deny-by-default
policy with explicit allowlists.

Security Model:
    1. DENY: Keys matching sensitive patterns (API keys, secrets, tokens)
    2. ALLOW: Minimal passthrough set (PATH, HOME, LANG, etc.)
    3. INJECT: Controlled gateway variables with validation

Pattern Matching Priority:
    1. Exact deny matches (highest priority)
    2. Prefix deny matches
    3. Regex pattern matches (API_KEY, SECRET, PASSWORD, TOKEN, CREDENTIAL)
    4. Allowlist passthrough (lowest priority)

Reference: WP-M5 (Secret Management), T9/D5 B3 (Gateway Injection)
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Final
from urllib.parse import urlparse

__all__ = [
    "DROPPED_PROXY_ENV",
    "is_allowed_gateway_inject",
    "is_denied_env_key",
    "sanitize_sandbox_env",
    "strip_proxy_env",
]


# =============================================================================
# Deny Patterns — Keys that must NEVER enter sandbox processes
# =============================================================================

# Prefixes that indicate sensitive provider/cloud keys
_DENIED_PREFIXES: Final[tuple[str, ...]] = (
    # AI/ML Provider API Keys
    "OPENAI_",
    "ANTHROPIC_",
    "OPENROUTER_",
    "LITELLM_",
    "HF_",
    "HUGGINGFACE_",
    "COHERE_",
    "GROQ_",
    "XAI_",
    "MISTRAL_",
    "TOGETHER_",
    "FIREWORKS_",
    "REPLICATE_",
    "PERPLEXITY_",
    # Cloud Provider Credentials
    "AWS_",
    "GCP_",
    "GOOGLE_APPLICATION_",
    "AZURE_",
    "CLOUDFLARE_",
    "DIGITALOCEAN_",
    "LINODE_",
    # Database Credentials
    "DATABASE_",
    "POSTGRES_",
    "MYSQL_",
    "REDIS_",
    "MONGO_",
    "ELASTICSEARCH_",
    # Internal Service Secrets
    "MODEL_GATEWAY_",
    "GATEWAY_TOKEN",
    "AUTH_TOKEN",
    "JWT_",
    "SECRET_",
    "PRIVATE_KEY",
    "SIGNING_KEY",
    "ENCRYPTION_KEY",
)

# Exact keys that are always denied (case-sensitive for performance)
_DENIED_EXACT: Final[frozenset[str]] = frozenset({
    # Gateway/Auth Tokens
    "GATEWAY_TOKEN",
    "MODEL_GATEWAY_TOKEN",
    "AUTH_TOKEN",
    "SESSION_SECRET",
    "COOKIE_SECRET",
    # Database URLs (contain credentials)
    "DATABASE_URL",
    "REDIS_URL",
    "MONGO_URL",
    "MONGODB_URI",
    # Provider API Keys
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    # Cloud Credentials
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "GOOGLE_CREDENTIALS",
    "AZURE_CLIENT_SECRET",
    # SSH/GPG
    "SSH_PRIVATE_KEY",
    "GPG_PRIVATE_KEY",
    # Generic Secrets
    "API_KEY",
    "API_SECRET",
    "PRIVATE_KEY",
    "MASTER_KEY",
})

# Compiled regex for sensitive key patterns (cached)
_SENSITIVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(API[_-]?KEY|SECRET|PASSWORD|TOKEN|CREDENTIAL|PRIVATE[_-]?KEY)",
    re.IGNORECASE,
)


# =============================================================================
# Allow Patterns — Minimal safe passthrough set
# =============================================================================

_ALLOWED_PASSTHROUGH: Final[frozenset[str]] = frozenset({
    # Path/Shell
    "PATH",
    "SHELL",
    "PWD",
    "HOME",
    "USER",
    "LOGNAME",
    # Locale
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_COLLATE",
    "LC_MESSAGES",
    "LC_NUMERIC",
    "LC_TIME",
    # Terminal
    "TERM",
    "COLORTERM",
    "COLUMNS",
    "LINES",
    # Temp directories
    "TMPDIR",
    "TMP",
    "TEMP",
    # System
    "TZ",
    "HOSTNAME",
    # Editor (safe, no secrets)
    "EDITOR",
    "VISUAL",
})


# =============================================================================
# Gateway Injection — Controlled sandbox-scoped variables
# =============================================================================

# Vendor hosts that must never be used as gateway base URLs
_VENDOR_HOSTS: Final[frozenset[str]] = frozenset({
    "api.openai.com",
    "openrouter.ai",
    "api.openrouter.ai",
    "api.anthropic.com",
    "api.cohere.ai",
    "api.mistral.ai",
    "api.together.xyz",
    "generativelanguage.googleapis.com",
})

_COMPACT_JWS: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

# Safe internal hostnames and patterns
_SAFE_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    ".internal",
    ".local",
    ".localhost",
    ".test",
    ".svc",
    ".svc.cluster.local",
)

_SAFE_HOSTS: Final[frozenset[str]] = frozenset({
    "model-gateway",
    "localhost",
    "127.0.0.1",
    "gateway",
    "yal-gateway",
    "litellm",
    "ollama",
})


# =============================================================================
# Proxy Environment Handling — Prevent bypass via caller proxy vars
# =============================================================================

# Proxy environment variables that should be stripped from caller environment.
# This prevents sandbox processes from inheriting host proxy settings that
# could bypass egress controls.
DROPPED_PROXY_ENV: Final[frozenset[str]] = frozenset({
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
    "FTP_PROXY",
    "ftp_proxy",
})


def strip_proxy_env(env: dict[str, str]) -> dict[str, str]:
    """Strip caller's proxy environment variables.

    Prevents sandbox from inheriting host proxy settings
    that could bypass egress controls.

    Args:
        env: Environment dictionary to filter.

    Returns:
        Environment with proxy vars removed.

    Example:
        >>> env = {"PATH": "/usr/bin", "HTTP_PROXY": "http://attacker.com"}
        >>> clean = strip_proxy_env(env)
        >>> "HTTP_PROXY" in clean
        False
    """
    return {k: v for k, v in env.items() if k not in DROPPED_PROXY_ENV}


# =============================================================================
# Core Functions
# =============================================================================

def is_denied_env_key(key: str) -> bool:
    """Check if an environment key should be denied from sandbox.

    Uses a three-tier matching strategy:
        1. Exact match against known sensitive keys
        2. Prefix match against provider/cloud patterns
        3. Regex match for generic sensitive patterns

    Args:
        key: Environment variable name to check.

    Returns:
        True if key should be denied (never enter sandbox).

    Example:
        >>> is_denied_env_key("OPENAI_API_KEY")
        True
        >>> is_denied_env_key("PATH")
        False
        >>> is_denied_env_key("MY_SECRET_VALUE")
        True
    """
    # Fast path: exact match (case-sensitive first, then uppercase)
    if key in _DENIED_EXACT:
        return True

    upper = key.upper()
    if upper in _DENIED_EXACT:
        return True

    # Prefix matching
    for prefix in _DENIED_PREFIXES:
        if upper.startswith(prefix):
            return True

    # Regex pattern matching for generic secrets
    if _SENSITIVE_PATTERN.search(upper):
        # Exception: allowlist keys that happen to match (e.g., PATH doesn't match anyway)
        return key not in _ALLOWED_PASSTHROUGH

    return False


@lru_cache(maxsize=128)
def _parse_and_validate_url(url: str) -> str | None:
    """Parse URL and return lowercase hostname, or None if invalid.

    Cached for repeated validation of the same URLs.
    """
    text = url.strip()
    if not text:
        return None

    # Add scheme if missing for proper parsing
    if "://" not in text:
        text = f"http://{text}"

    try:
        parsed = urlparse(text)
        hostname = parsed.hostname
        return hostname.lower() if hostname else None
    except ValueError:
        return None


def _is_safe_gateway_host(host: str) -> bool:
    """Check if hostname is safe for gateway injection.

    Safe hosts include:
        - Known internal service names
        - Private IP ranges
        - Internal/local suffixes
        - Single-word hostnames (Docker Compose style)
    """
    # Explicitly denied vendor hosts
    if host in _VENDOR_HOSTS:
        return False

    # Explicitly allowed hosts
    if host in _SAFE_HOSTS:
        return True

    # Check dynamic GATEWAY_BASE_URL host
    gateway_base = os.environ.get("GATEWAY_BASE_URL", "")
    if gateway_base:
        gateway_host = _parse_and_validate_url(gateway_base)
        if gateway_host and host == gateway_host:
            return True

    # Safe suffixes (internal, local, test, k8s)
    for suffix in _SAFE_HOST_SUFFIXES:
        if host.endswith(suffix):
            return True

    # Private IP ranges
    if (host.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31."))):
        return True

    # Single-word hostname = Docker Compose DNS (no TLD)
    return "." not in host


def is_allowed_gateway_inject(key: str, value: str) -> bool:
    """Check if a gateway-related env var can be injected into sandbox.

    This validates intentional session-scoped gateway configuration.
    Only specific keys with validated values are allowed.

    Args:
        key: Environment variable name.
        value: Environment variable value.

    Returns:
        True if this is a safe gateway injection.

    Allowed Patterns:
        - OPENAI_BASE_URL: Must point to internal/gateway host (not vendor APIs)
        - OPENAI_API_KEY: Must start with "vk_" (virtual key prefix)
        - YAL_VK_BIND_PATH: Any non-empty path
        - OPENAI_API_KEY_FILE: Any non-empty path
        - BROWSE_LAB_MODEL_TOKEN: A compact signed token (the browser capability)

    Example:
        >>> is_allowed_gateway_inject("OPENAI_BASE_URL", "http://gateway:8000")
        True
        >>> is_allowed_gateway_inject("OPENAI_BASE_URL", "https://api.openai.com")
        False
        >>> is_allowed_gateway_inject("OPENAI_API_KEY", "vk_session_abc123")
        True
    """
    upper = key.upper()
    text = value if isinstance(value, str) else ""

    if upper == "OPENAI_BASE_URL":
        host = _parse_and_validate_url(text)
        return host is not None and _is_safe_gateway_host(host)

    if upper == "OPENAI_API_KEY":
        # Only virtual keys (vk_ prefix) are allowed
        return text.startswith("vk_")

    if upper in ("YAL_VK_BIND_PATH", "OPENAI_API_KEY_FILE"):
        # Path to key file - any non-empty value is acceptable
        return bool(text.strip())

    if upper == "BROWSE_LAB_MODEL_TOKEN":
        # A browser capability: a signed, hour-long grant for one session's model,
        # made to be held by the browse runner. Never a vendor or gateway key.
        return _COMPACT_JWS.fullmatch(text) is not None

    return False


def sanitize_sandbox_env(
    extra: dict[str, str] | None = None,
    *,
    base: dict[str, str] | None = None,
    strip_proxy: bool = True,
) -> dict[str, str]:
    """Build sanitized environment for sandbox process.

    Implements defense-in-depth:
        1. Start with allowlisted keys from base/host environment
        2. Strip proxy vars to prevent egress bypass (if strip_proxy=True)
        3. Apply extra vars with gateway injection validation
        4. Filter all vars through deny patterns

    Args:
        extra: Additional env vars to include (also filtered).
        base: Base environment (defaults to os.environ).
        strip_proxy: Strip caller's proxy env vars (default True).
                     This prevents sandbox from inheriting host proxy
                     settings that could bypass egress controls.

    Returns:
        Sanitized environment dict safe for sandbox processes.

    Example:
        >>> env = sanitize_sandbox_env({"MY_VAR": "value"})
        >>> "PATH" in env  # From host allowlist
        True
        >>> "OPENAI_API_KEY" in env  # Denied
        False
        >>> "HTTP_PROXY" in env  # Stripped
        False
    """
    source = base if base is not None else dict(os.environ)
    result: dict[str, str] = {}

    # Phase 1: Copy allowlisted keys from source
    for key, value in source.items():
        if key in _ALLOWED_PASSTHROUGH and not is_denied_env_key(key):
            result[key] = value

    # Phase 2: Process extra vars
    if extra:
        for key, value in extra.items():
            # Gateway injections bypass deny check (already validated)
            if is_allowed_gateway_inject(key, value):
                result[key] = value
                continue

            # All other vars must pass deny check
            if not is_denied_env_key(key):
                result[key] = value

    # Phase 3: Strip proxy vars if requested (default)
    if strip_proxy:
        result = strip_proxy_env(result)

    return result


# =============================================================================
# Class-Based API (Alternative)
# =============================================================================

