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

from supervisor.sandbox.security.env import DROPPED_PROXY_ENV

logger = logging.getLogger(__name__)

__all__ = [
    "DROPPED_PROXY_ENV",
    "EgressPolicy",
    "ProxyEnvVars",
]


# =============================================================================
# Constants
# =============================================================================

# One list of proxy variables, kept with the rest of the sandbox environment rules.


# =============================================================================
# Errors
# =============================================================================


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


