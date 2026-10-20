"""Minimal ACP server per contracts/mcp_acp.md (MVP wire): records every call."""
from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import FastAPI, Request

LOG = os.environ.get("ACP_LOG", "acp.jsonl")
app = FastAPI()

def _log(path: str, body: dict) -> None:
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"at": time.time(), "path": path, "body": body}) + "\n")

@app.post("/v1/initialize")
async def initialize(request: Request) -> dict:
    _log("/v1/initialize", await request.json()); return {"protocolVersion": 1}

@app.post("/v1/run")
async def run(request: Request) -> dict:
    body = await request.json(); _log("/v1/run", body)
    return {"text": f"acp-child did: {body.get('prompt', '')[:80]}", "child_id": f"acp-{uuid.uuid4().hex[:8]}", "status": "ok"}

@app.post("/v1/jobs")
async def jobs(request: Request) -> dict:
    body = await request.json(); _log("/v1/jobs", body)
    return {"job_id": f"acp-job-{uuid.uuid4().hex[:8]}", "status": "running"}

@app.post("/v1/children/{child_id}/{action}")
async def child(child_id: str, action: str, request: Request) -> dict:
    _log(f"/v1/children/{child_id}/{action}", await request.json()); return {}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
