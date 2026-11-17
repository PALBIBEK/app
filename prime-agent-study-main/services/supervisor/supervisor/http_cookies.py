"""A cookie jar that keeps none, and the connection limits, for HTTP clients shared across
sessions and tenants.

One client serves every caller in a process (``models.client.ModelRouter``,
``memory.client.NotebookClient``), and httpx keeps the cookies responses set: a balancer's
sticky cookie or a vendor's bot-management cookie set on one session's call would go out
on every later session's, and every tenant's. (The model gateway keeps a copy of its own,
``gateway.providers.http``: it imports nothing of the rest of the supervisor.)
"""

from __future__ import annotations

import http.cookiejar

import httpx

__all__ = ["SHARED_LIMITS", "no_cookies"]

SHARED_LIMITS = httpx.Limits(max_connections=None, max_keepalive_connections=64, keepalive_expiry=5.0)
"""Connections of a client every caller shares: bounded by the threads that make calls
(``supervisor.blocking``), not by httpx's 100, which a busy worker would wait on; up to 64
kept open between calls, each 5 s idle (well inside a balancer's idle timeout). The model
gateway keeps the same in its own module (it imports nothing outside itself)."""


def no_cookies() -> http.cookiejar.CookieJar:
    """A jar whose policy accepts no cookie from any domain."""
    return http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
