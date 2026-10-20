"""The credential proxy: tenant secrets authenticate the supervisor's calls to tool servers.

A tenant stores a tool server's credential once (``PUT /v1/secrets/{name}``),
named for the server's origin: ``mcp:https://tools.example.com``. Every session
of that tenant then calls that server with it, from the supervisor. Nothing in a
sandbox, a prompt or the journal holds it; the secrets API never returns a value.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from supervisor.vault.store import Owner, Vault

if TYPE_CHECKING:
    from supervisor.seams.interfaces.tool_provider import ToolProvider

__all__ = ["CACHE_S", "ToolCredentials", "mcp_secret_name", "tool_servers"]

CACHE_S: Final = 60.0
"""How long a looked-up credential is reused: a rotated secret reaches live sessions this fast."""


def mcp_secret_name(server_url: str) -> str:
    """The vault name of the credential for the MCP server at ``server_url`` (its origin)."""
    parts = urlsplit(server_url.strip())
    return f"mcp:{parts.scheme.lower()}://{parts.netloc.lower()}"


def tool_servers(provider: ToolProvider | None) -> list[object]:
    """The MCP providers inside a (possibly composite) tool provider."""
    from supervisor.seams.providers.tools.composite_tools import CompositeToolProvider
    from supervisor.seams.providers.tools.mcp_tools import McpToolProvider

    if isinstance(provider, McpToolProvider):
        return [provider]
    if isinstance(provider, CompositeToolProvider):
        return [found for inner in provider.providers for found in tool_servers(inner)]
    return []


class ToolCredentials:
    __slots__ = ("_cache", "_vault")

    def __init__(self, vault: Vault) -> None:
        self._vault = vault
        self._cache: dict[tuple[str, str], tuple[float, str | None]] = {}

    async def lookup(self, tenant_id: str, name: str) -> str | None:
        key = (tenant_id, name)
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < CACHE_S:
            return hit[1]
        secret = await self._vault.get(Owner.tenant(tenant_id), name)
        value = secret.value if secret is not None else None
        self._cache[key] = (time.monotonic(), value)
        return value

    async def authorize(self, tenant_id: str, provider: ToolProvider | None) -> int:
        """Give each MCP server in ``provider`` the tenant's credential for it; returns how many had one."""
        from supervisor.seams.providers.tools.mcp_tools import McpToolProvider

        found = 0
        for server in tool_servers(provider):
            assert isinstance(server, McpToolProvider)
            secret = await self.lookup(tenant_id, mcp_secret_name(server.server_url))
            server.authorize(secret)
            found += secret is not None
        return found

    def forget(self, tenant_id: str, name: str) -> None:
        """Drop a cached credential (it was changed or deleted through this process)."""
        self._cache.pop((tenant_id, name), None)
