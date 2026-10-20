"""One process serves the API, the gateway and the memory service: each on its own loop.

Found on the local stack with all three in one process: at publish the API reads
the gateway's model list with a blocking call; with both servers on one event
loop the gateway could not answer until the call timed out, and the check let
an unknown model through. Here a first app calls a second one the same way.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from supervisor.main import _flag

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = """
import sys, httpx, uvicorn
from fastapi import FastAPI
from supervisor.main import serve

first, second = FastAPI(), FastAPI()

@second.get("/models")
def models():
    return {"models": ["echo"]}

@first.get("/ask")
def ask():  # a blocking call to the other listener, as the API's catalog read is
    return httpx.get(f"http://127.0.0.1:{sys.argv[2]}/models", timeout=3).json()

serve([
    ("first", uvicorn.Config(first, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")),
    ("second", uvicorn.Config(second, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")),
])
"""


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _up(url: str, seconds: float = 20) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            httpx.get(url, timeout=1)
            return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise TimeoutError(url)


def test_a_listener_calling_another_is_answered_and_sigterm_stops_both(tmp_path: Path) -> None:
    script = tmp_path / "serve_two.py"
    script.write_text(SCRIPT)
    a, b = _port(), _port()
    proc = subprocess.Popen([sys.executable, str(script), str(a), str(b)], cwd=str(ROOT))
    try:
        _up(f"http://127.0.0.1:{b}/models")
        _up(f"http://127.0.0.1:{a}/docs")
        started = time.monotonic()
        answer = httpx.get(f"http://127.0.0.1:{a}/ask", timeout=10).json()
        assert answer == {"models": ["echo"]}
        assert time.monotonic() - started < 2, "the call waited on its own loop"
        proc.send_signal(signal.SIGTERM)
        # uvicorn re-raises the SIGTERM it handled once shut down, as when it runs alone.
        assert proc.wait(timeout=15) in (0, -signal.SIGTERM)
        for port in (a, b):  # both listeners are gone, not only the first
            try:
                httpx.get(f"http://127.0.0.1:{port}/docs", timeout=1)
            except httpx.HTTPError:
                continue
            raise AssertionError(f"port {port} still serving")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_each_listener_is_on_unless_its_flag_says_off(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    for value, on in (("1", True), ("true", True), ("0", False), ("off", False), ("no", False)):
        monkeypatch.setenv("SERVE_MODEL_GATEWAY", value)
        assert _flag("SERVE_MODEL_GATEWAY") is on, value
    monkeypatch.delenv("SERVE_MODEL_GATEWAY")
    assert _flag("SERVE_MODEL_GATEWAY") is True
    assert os.environ.get("SERVE_MODEL_GATEWAY") is None
