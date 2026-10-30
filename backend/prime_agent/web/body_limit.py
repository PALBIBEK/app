"""Request bodies past a size limit are refused (413) before they are read into memory.

There was no limit: a 64 MB JSON body was read and parsed whole (about three times its
size in memory) before validation refused it, so a few large requests at once could take
the API down. Now a declared ``Content-Length`` over the limit is refused at once, and a
body sent without one (chunked) is refused as soon as it passes the limit.

The limit is ``MAX_REQUEST_BYTES`` (2 MiB): JSON requests are small (a message is at most
64K characters). The one upload that goes through the API, a workspace file when storage
cannot take a direct upload, gets the workspace's file limit plus room for the multipart
framing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = ["MAX_REQUEST_BYTES", "MULTIPART_OVERHEAD_BYTES", "LimitRequestBody", "request_limit"]

MAX_REQUEST_BYTES: Final = 2 * 1024 * 1024
MULTIPART_OVERHEAD_BYTES: Final = 64 * 1024
_UPLOAD: Final = re.compile(r"/workspace/scopes/[^/]+/files/")


def request_limit(prefix: str, max_upload_bytes: int) -> Callable[[Scope], int]:
    """The limit for a request: the file limit for the proxied workspace upload, else 2 MiB."""
    upload = re.compile(re.escape(prefix) + _UPLOAD.pattern)

    def limit(scope: Scope) -> int:
        if scope.get("method") == "PUT" and upload.match(scope.get("path", "")):
            return max_upload_bytes + MULTIPART_OVERHEAD_BYTES
        return MAX_REQUEST_BYTES

    return limit


class LimitRequestBody:
    """ASGI middleware: refuse a request body larger than ``limit_for(scope)`` bytes.

    A body that passes the limit as it arrives is answered 413 at that moment; the route
    then sees the client gone (FastAPI would turn an exception raised while it reads into a
    400), and whatever it still sends is dropped.
    """

    def __init__(self, app: ASGIApp, *, limit_for: Callable[[Scope], int]) -> None:
        self._app = app
        self._limit_for = limit_for

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        declared = dict(scope.get("headers") or ()).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await _refuse(send, limit)
            return
        received, started, refused = 0, False, False

        async def counting_receive() -> Message:
            nonlocal received, refused
            if refused:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit and not started:
                    refused = True
                    await _refuse(send, limit)
                    return {"type": "http.disconnect"}
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if refused:
                return
            started = started or message["type"] == "http.response.start"
            await send(message)

        await self._app(scope, counting_receive, tracking_send)


async def _refuse(send: Send, limit: int) -> None:
    body = json.dumps(
        {"detail": {"error_code": "REQUEST_TOO_LARGE", "message": f"the request body may be at most {limit} bytes"}}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})
