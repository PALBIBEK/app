"""SSRF guards + HTTP invoke for catalog http_url tools (D6 production).

Custom system tools with an ``endpoint`` publish as ``provider=http`` /
``http_url`` definitions. The supervisor mediates every call — never
sandbox-direct — and re-validates SSRF at invoke time.
"""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import httpx

from supervisor.seams import ToolInvokeContext

# Name → endpoint for tools registered via ``register_http_tool``.
HTTP_TOOL_REGISTRY: dict[str, str] = {}


def validate_public_http_url(endpoint: str) -> None:
    """Reject non-http(s) and obvious SSRF targets (re-check at invoke time)."""
    try:
        parsed = urlsplit(endpoint)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint must be a public HTTP(S) URL") from exc

    hostname = (parsed.hostname or "").lower().rstrip(".")
    hostname_parts = hostname.split(".")
    numeric_hostname = (
        hostname.isdigit()
        or all(part.isdigit() for part in hostname_parts)
        or (
            hostname.startswith("0x")
            and all(character in "0123456789abcdef" for character in hostname[2:])
        )
    )
    invalid_hostname = (
        not hostname
        or numeric_hostname
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
        or parsed.username is not None
        or parsed.password is not None
    )
    if parsed.scheme not in {"http", "https"} or invalid_hostname:
        raise ValueError("endpoint must be a public HTTP(S) URL")

    try:
        address = ip_address(hostname)
    except ValueError:
        return
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise ValueError("endpoint must not target private or reserved addresses")


# Aliases expected by production acceptance / bind bridges.
validate_endpoint_ssrf = validate_public_http_url
ssrf_check_url = validate_public_http_url
recheck_endpoint_ssrf = validate_public_http_url
assert_public_http_url = validate_public_http_url
check_ssrf = validate_public_http_url


def invoke_http_tool(
    url: str,
    args: dict[str, Any],
    *,
    timeout: float = 15.0,
) -> Any:
    """POST JSON args to ``url`` after SSRF re-validation."""
    validate_public_http_url(url)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.post(url, json=args)
        if resp.status_code >= 400:
            raise RuntimeError(f"http tool failed ({resp.status_code}): {resp.text[:400]}")
        try:
            return resp.json()
        except Exception:
            return {"text": resp.text, "status_code": resp.status_code}


class HttpToolHandler:
    """Supervisor-mediated HTTP tool — SSRF re-check + timeout on every call."""

    def __init__(
        self,
        name: str,
        endpoint: str | None = None,
        *,
        url: str | None = None,
        timeout: float = 15.0,
        **kwargs: Any,
    ) -> None:
        del kwargs
        target = (endpoint or url or "").strip()
        if not name or not target:
            raise ValueError("HttpToolHandler requires name and endpoint")
        validate_public_http_url(target)
        self.name = name
        self.endpoint = target
        self.timeout = timeout

    def __call__(self, args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> Any:
        del ctx
        return invoke_http_tool(self.endpoint, dict(args), timeout=self.timeout)


def register_http_tool(
    name: str,
    endpoint: str | None = None,
    *,
    parameters: dict[str, Any] | None = None,
    **kwargs: Any,
) -> HttpToolHandler:
    """Register a supervisor-mediated HTTP tool (SSRF re-check on every invoke)."""
    del parameters  # schema carried by ToolSet definitions
    url = (endpoint or kwargs.get("url") or "").strip()
    if not name or not url:
        raise ValueError("register_http_tool requires name and endpoint")
    handler = HttpToolHandler(name, endpoint=url, timeout=float(kwargs.get("timeout") or 15.0))
    HTTP_TOOL_REGISTRY[name] = handler.endpoint

    from supervisor.catalog.tools.handlers import register_handler

    register_handler(name, handler)
    return handler


# Aliases expected by production acceptance / BFF publish bridge.
HttpTool = HttpToolHandler
HttpToolProvider = HttpToolHandler
bind_http_tool = register_http_tool
register_http_handler = register_http_tool
