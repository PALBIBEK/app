"""A cookie jar that keeps none, for HTTP clients shared across sessions and tenants.

One client serves every caller in a process (``models.client.ModelRouter``,
``memory.client.NotebookClient``), and httpx keeps the cookies responses set: a balancer's
sticky cookie or a vendor's bot-management cookie set on one session's call would go out
on every later session's, and every tenant's. (The model gateway keeps a copy of its own,
``gateway.providers.http``: it imports nothing of the rest of the supervisor.)
"""

from __future__ import annotations

import http.cookiejar

__all__ = ["no_cookies"]


def no_cookies() -> http.cookiejar.CookieJar:
    """A jar whose policy accepts no cookie from any domain."""
    return http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
