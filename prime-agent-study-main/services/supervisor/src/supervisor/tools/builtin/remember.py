from __future__ import annotations

import asyncio
from typing import Any

from supervisor.seams.services import (
    memory_service_base_url,
    resolve_memory_scope,
    write_memory_item,
)
from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments, UnsupportedCapability
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass


class RememberArgs(ToolArgs):
    content: str
    """Fact or note to remember"""


@tool(tool_class=ToolClass.IDEMPOTENT)
async def remember(args: RememberArgs, ctx: ToolContext) -> dict[str, Any]:
    """Store a durable fact in long-term memory (append-only, safe to retry).

    Scoped to the calling session's tenant; the scope is never taken from the
    model's arguments.
    """
    if not args.content.strip():
        raise InvalidArguments("content must not be empty")
    if not memory_service_base_url():
        raise UnsupportedCapability("long-term memory is not configured for this deployment")
    scope = resolve_memory_scope(session_id=ctx.session_id or "", tenant_id=ctx.tenant_id or "")
    return await asyncio.to_thread(
        write_memory_item,
        content=args.content,
        scope=scope,
        source_session=ctx.session_id,
    )
