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
import re
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

GATEWAY = os.environ.get("RELAY_GATEWAY", "http://127.0.0.1:8090")
TOKEN = os.environ.get("RELAY_GATEWAY_TOKEN", "dev-local-gateway-token")
LOG = os.environ.get("RELAY_LOG", "relay.jsonl")

EXTRACTION = "You extract durable observations"
"""How automatic capture's extraction prompt starts (supervisor.memory.extraction)."""
FACT = re.compile(r"zq[0-9a-f]+-[A-Za-z0-9.-]+?qz")
"""A load test's fact token (e2e/bench_memory.py)."""

app = FastAPI()
# Uncapped: under a load test the relay must not be the stack's bottleneck (httpx caps a
# client at 100 connections, which the slow test switch's held streams fill).
client = httpx.AsyncClient(timeout=120, limits=httpx.Limits(max_connections=None, max_keepalive_connections=200))


def _record(path: str, request: Request, body: dict) -> None:
    seen = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "x-api-key")}
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "path": path, "headers": seen, "body": body}) + "\n")


def _extraction_answer(body: dict) -> str | None:
    """Test switch CAPTUREMODEL: memory's extraction, answered as a model would that found
    the facts the transcript states (each load-test token in it, wherever it was: the
    user's words or a tool's output). Not this switch's request: None."""
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    system = next((m.get("content") for m in messages if m.get("role") == "system"), "")
    text = json.dumps(body)
    if "CAPTUREMODEL" not in text or not str(system).startswith(EXTRACTION):
        return None
    tokens = list(dict.fromkeys(FACT.findall(text)))
    observations = [
        {"type": "user", "topic_hint": "locker", "statement": f"The locker word is {t}.", "keywords": [t],
         "aliases": [], "body": None}
        for t in tokens
    ]
    return json.dumps({
        "outcome": "observations" if observations else "noop",
        "observations": observations,
        "sensitivity": "ordinary",
    })


def _completion(body: dict, answer: str) -> object:
    """``answer`` as the model's reply, in the chat wire's shape (a stream when asked). Not
    through echo, whose reply begins "echo:": a strict outcome parser rightly refuses that."""
    model = str(body.get("model") or "relay")
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    if not body.get("stream"):
        return JSONResponse({
            "id": "relay-extraction", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
            "usage": usage,
        })

    def chunk(delta: dict, finish: str | None, **extra: object) -> bytes:
        payload = {"id": "relay-extraction", "object": "chat.completion.chunk", "created": int(time.time()),
                   "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
        return f"data: {json.dumps(payload)}\n\n".encode()

    def events():
        yield chunk({"role": "assistant", "content": answer}, None)
        yield chunk({}, "stop", usage=usage)
        yield b"data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


async def _relay(path: str, request: Request) -> object:
    body = await request.json()
    _record(path, request, body)
    if (answer := _extraction_answer(body)) is not None:
        return _completion(body, answer)
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
