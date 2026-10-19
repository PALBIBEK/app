"""Domain-level egress control and proxy enforcement.

This module provides egress policy management that controls which domains
a sandbox can access. Traffic is forced through a controlled proxy.

Design (matches QM's egress handling):
    1. Strip caller's proxy environment variables
    2. Inject controlled proxy configuration
    3. Domain-level allowlist enforcement

Security:
    - Caller proxy vars stripped to prevent bypass
    - All traffic forced through controlled proxy
    - Domain allowlist prevents arbitrary egress

Reference:
    - QM: sandbox-env.ts forceThroughProxyEnv, DROPPED_PROXY_ENV
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

_log = logging.getLogger(__name__)

__all__ = [
    "EgressPolicy",
    "ProxyEnvVars",
    "EgressPolicyError",
    "DROPPED_PROXY_ENV",
    "strip_proxy_env",
    "inject_proxy_env",
    "force_through_proxy_env",
]


# =============================================================================
# Constants
# =============================================================================

# Proxy environment variables that should be stripped from caller
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


# =============================================================================
# Errors
# =============================================================================


class EgressPolicyError(Exception):
    """Egress policy configuration failed.

    Attributes:
        message: Error description.
        domain: Affected domain, if known.
        retryable: Whether the operation can be retried.
    """

    def __init__(
        self,
        message: str,
        *,
        domain: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.domain = domain
        self.retryable = retryable


# =============================================================================
# Data Classes
# =============================================================================


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Domain-level egress policy.

    Attributes:
        allowed_domains: Set of allowed domains (exact match).
        proxy_url: Proxy URL to route traffic through.
        created_at_ms: Policy creation timestamp in milliseconds.
    """

    allowed_domains: frozenset[str]
    proxy_url: str | None = None
    created_at_ms: int = 0

    @classmethod
    def allow_all(cls) -> EgressPolicy:
        """Create policy that allows all egress (no restrictions)."""
        return cls(allowed_domains=frozenset())

    @classmethod
    def deny_all(cls) -> EgressPolicy:
        """Create policy that denies all egress."""
        return cls(allowed_domains=frozenset({"<none>"}))


@dataclass(frozen=True, slots=True)
class ProxyEnvVars:
    """Proxy environment variables to inject.

    Attributes:
        http_proxy: HTTP proxy URL.
        https_proxy: HTTPS proxy URL.
        no_proxy: Comma-separated list of hosts to bypass proxy.
    """

    http_proxy: str
    https_proxy: str
    no_proxy: str = "localhost,127.0.0.1,::1"

    def as_dict(self) -> dict[str, str]:
        """Convert to environment variable dictionary.

        Returns both lowercase and uppercase variants for compatibility.
        """
        return {
            "HTTP_PROXY": self.http_proxy,
            "http_proxy": self.http_proxy,
            "HTTPS_PROXY": self.https_proxy,
            "https_proxy": self.https_proxy,
            "NO_PROXY": self.no_proxy,
            "no_proxy": self.no_proxy,
        }


# =============================================================================
# Functions
# =============================================================================


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


def inject_proxy_env(
    env: dict[str, str],
    proxy_vars: ProxyEnvVars,
) -> dict[str, str]:
    """Inject proxy environment variables.

    Forces traffic through specified proxy.

    Args:
        env: Base environment dictionary.
        proxy_vars: Proxy configuration to inject.

    Returns:
        Environment with proxy vars injected.

    Example:
        >>> env = {"PATH": "/usr/bin"}
        >>> proxy = ProxyEnvVars(http_proxy="http://proxy:8080", https_proxy="http://proxy:8080")
        >>> result = inject_proxy_env(env, proxy)
        >>> result["HTTP_PROXY"]
        'http://proxy:8080'
    """
    result = strip_proxy_env(env)
    result.update(proxy_vars.as_dict())
    return result


def force_through_proxy_env(
    proxy_url: str,
    token: str | None = None,
    *,
    no_proxy: str = "localhost,127.0.0.1,::1",
) -> dict[str, str]:
    """Generate proxy environment variables for controlled egress.

    Matches QM's forceThroughProxyEnv pattern.

    Args:
        proxy_url: Proxy URL (e.g., "http://proxy.internal:8080").
        token: Optional auth token to embed in URL.
        no_proxy: Hosts to bypass proxy.

    Returns:
        Dictionary of proxy environment variables.

    Example:
        >>> env = force_through_proxy_env("http://proxy:8080", token="secret123")
        >>> "secret123" in env["HTTP_PROXY"]
        True
    """
    from urllib.parse import urlparse

    parsed = urlparse(proxy_url)

    if token:
        # Embed token in URL: http://x:TOKEN@host:port
        auth_url = f"{parsed.scheme}://x:{token}@{parsed.netloc}"
    else:
        auth_url = proxy_url

    return {
        "HTTP_PROXY": auth_url,
        "http_proxy": auth_url,
        "HTTPS_PROXY": auth_url,
        "https_proxy": auth_url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }
