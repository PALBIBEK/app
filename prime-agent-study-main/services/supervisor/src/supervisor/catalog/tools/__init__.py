"""Catalog-registered tools that live outside the supervisor (HTTP endpoints).

Built-in tool implementations live in ``supervisor.tools``; this package only
adapts remote endpoints into that registry.
"""

from supervisor.catalog.tools.http_tools import (
    HttpToolHandler,
    HttpToolProvider,
    register_http_tool,
    validate_public_http_url,
)

__all__ = [
    "HttpToolHandler",
    "HttpToolProvider",
    "register_http_tool",
    "validate_public_http_url",
]
