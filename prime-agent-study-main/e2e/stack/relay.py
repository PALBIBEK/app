"""Recording relay: the upstream "vendor" behind the gateway's test routes.

The gateway's openai/anthropic providers are pointed here. Every request the
gateway sends upstream is appended to RELAY_LOG (one JSON object per line: wire,
path, headers seen, body), then forwarded to the gateway's own echo route on the
same wire, so replies keep echo's semantics (``/tool`` directives, signed
thinking, encrypted reasoning). Run: python relay.py
"""

from __future__ import annotations

import json
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

GATEWAY = os.environ.get("RELAY_GATEWAY", "http://127.0.0.1:8090")
TOKEN = os.environ.get("RELAY_GATEWAY_TOKEN", "dev-local-gateway-token")
LOG = os.environ.get("RELAY_LOG", "relay.jsonl")

app = FastAPI()
client = httpx.AsyncClient(timeout=120)


def _record(path: str, request: Request, body: dict) -> None:
    seen = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "x-api-key")}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "path": path, "headers": seen, "body": body}) + "\n")


async def _relay(path: str, request: Request) -> object:
    body = await request.json()
    _record(path, request, body)
    if "SLOWMODEL" in json.dumps(body):  # test switch: a model call that takes a while
        import asyncio

        await asyncio.sleep(float(os.environ.get("RELAY_SLOW_S", "20")))
    forwarded = dict(body, model="echo")
    if "LOOPMODEL" in json.dumps(body) and isinstance(body.get("messages"), list):
        # Test switch: a model stuck in a loop. Hide the tool results from echo, so it
        # repeats the user's /tool directive (same call, same arguments) every step.
        msgs = body["messages"]
        last_user = max(i for i, m in enumerate(msgs) if m.get("role") == "user")
        forwarded["messages"] = msgs[: last_user + 1]
    headers = {"Authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
    if body.get("stream"):
        upstream = client.build_request("POST", f"{GATEWAY}{path}", json=forwarded, headers=headers)
        response = await client.send(upstream, stream=True)

        async def chunks():
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                await response.aclose()

        return StreamingResponse(
            chunks(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "text/event-stream"),
        )
    response = await client.post(f"{GATEWAY}{path}", json=forwarded, headers=headers)
    return JSONResponse(response.json(), status_code=response.status_code)


@app.post("/v1/chat/completions")
async def chat(request: Request) -> object:
    return await _relay("/v1/chat/completions", request)


@app.post("/v1/messages")
async def messages(request: Request) -> object:
    return await _relay("/v1/messages", request)


@app.post("/v1/responses")
async def responses(request: Request) -> object:
    return await _relay("/v1/responses", request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("RELAY_PORT", "9100")), log_level="warning")
