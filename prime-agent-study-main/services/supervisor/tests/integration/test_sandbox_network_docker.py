"""A ``local_docker`` sandbox's network, by its profile, against a real Docker daemon.

The machine running the tests stands in for the internet: a TLS server on 443 and a plain
one on 80, reached as ``allowed.test`` and ``denied.test`` (the egress gate resolves both
to it). Each mode is checked from inside a sandbox, with curl, and the browser code mode
browses through the gate.

Needs a Docker daemon, ports 80 and 443 free here, and ``SUPERVISOR_TEST_BROWSER_IMAGE``
(an image with Python, curl, Playwright and Chromium); skipped otherwise."""

from __future__ import annotations

import asyncio
import os
import ssl
import subprocess
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from supervisor.catalog import NetworkAccess, NetworkMode
from supervisor.sandbox.network import NetworkNotEnforceable
from supervisor.sandbox.providers.docker_network import LABEL_EGRESS, OPEN_NETWORK
from supervisor.sandbox.providers.local_docker import LocalDockerProvider
from supervisor.sandbox.providers.local_process import LocalProcessProvider
from supervisor.tools import default_registry
from supervisor.tools.context import ToolContext
from supervisor.tools.workspace import SandboxWorkspace

IMAGE = os.environ.get("SUPERVISOR_TEST_BROWSER_IMAGE", "")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not IMAGE, reason="no sandbox image with curl and a browser")]

ALLOW = NetworkAccess(NetworkMode.ALLOW, ("allowed.test",))
OPEN = NetworkAccess(NetworkMode.OPEN)


class _Hello(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<title>outside</title>hello from outside"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture(scope="module")
def outside(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """The stand-in internet: HTTPS on 443 (a self-signed certificate) and HTTP on 80."""
    certs = tmp_path_factory.mktemp("certs")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=allowed.test",
         "-keyout", str(certs / "key.pem"), "-out", str(certs / "cert.pem")],
        check=True, capture_output=True,
    )
    tls = ThreadingHTTPServer(("0.0.0.0", 443), _Hello)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certs / "cert.pem", certs / "key.pem")
    tls.socket = context.wrap_socket(tls.socket, server_side=True)
    plain = ThreadingHTTPServer(("0.0.0.0", 80), _Hello)
    for server in (tls, plain):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield
    for server in (tls, plain):
        server.shutdown()


@pytest.fixture
def provider() -> LocalDockerProvider:
    return LocalDockerProvider(egress_gate_hosts={"allowed.test": "host-gateway", "denied.test": "host-gateway"})


@pytest.fixture
async def made(provider: LocalDockerProvider) -> AsyncIterator[list[Any]]:
    """The sandboxes a test made, destroyed after it."""
    boxes: list[Any] = []
    yield boxes
    for box in boxes:
        await box.destroy()


async def _sandbox(provider: LocalDockerProvider, made: list[Any], network: NetworkAccess | None, **labels: str) -> Any:
    box = await provider.create(image=IMAGE, network=network, labels=labels or None)
    made.append(box)
    return box


async def _sh(box: Any, command: str) -> str:
    done = await box.exec(["sh", "-c", command], timeout=30)
    return str(done.stdout_text).strip()


def _docker(*args: str) -> str:
    """The docker CLI's answer (run in a thread from the tests: it blocks)."""
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout.strip()


async def _gateway() -> str:
    """The open network's gateway: the machine running the tests, from a sandbox."""
    return await asyncio.to_thread(_docker, "network", "inspect", OPEN_NETWORK, "-f", "{{(index .IPAM.Config 0).Gateway}}")


CURL = "curl -sk --max-time 5 -o - -w ' %{http_code}' "


@pytest.mark.usefixtures("outside")
async def test_no_network_reaches_nothing(provider: LocalDockerProvider, made: list[Any]) -> None:
    box = await _sandbox(provider, made, NetworkAccess(NetworkMode.NONE))
    await _sandbox(provider, made, OPEN)  # so the open network (and its gateway) exists

    assert (await _sh(box, f"{CURL}https://{await _gateway()}/ || true")).endswith("000")


@pytest.mark.usefixtures("outside")
async def test_open_reaches_out_but_never_across_to_another_sandbox(provider: LocalDockerProvider, made: list[Any]) -> None:
    one, other = await _sandbox(provider, made, OPEN), await _sandbox(provider, made, OPEN)
    await _sh(other, "nohup python3 -m http.server 8000 >/dev/null 2>&1 & sleep 1")
    other_ip = await _sh(other, "hostname -i")

    assert (await _sh(one, f"{CURL}https://{await _gateway()}/")).endswith("hello from outside 200")
    assert (await _sh(one, f"{CURL}http://{other_ip}:8000/ || true")).endswith("000"), "one sandbox reached another"


@pytest.mark.usefixtures("outside")
async def test_allow_reaches_only_its_hosts(provider: LocalDockerProvider, made: list[Any]) -> None:
    box = await _sandbox(provider, made, ALLOW)
    gate = (await _sh(box, "getent hosts allowed.test")).split()[0]

    assert (await _sh(box, f"{CURL}https://allowed.test/")).endswith("hello from outside 200")
    assert (await _sh(box, f"{CURL}http://allowed.test/")).endswith("hello from outside 200")
    # Another name, even sent to the gate by hand: refused there.
    assert (await _sh(box, f"{CURL}--resolve denied.test:443:{gate} https://denied.test/ || true")).endswith("000")
    assert (await _sh(box, f"{CURL}--resolve denied.test:80:{gate} http://denied.test/ || true")).endswith("000")
    # Around the gate, by address: no route out.
    assert (await _sh(box, f"{CURL}https://{await _gateway()}/ || true")).endswith("000")


async def test_destroying_an_allow_sandbox_removes_its_gate_and_network(provider: LocalDockerProvider, made: list[Any]) -> None:
    box = await _sandbox(provider, made, ALLOW)
    token = box.info.labels[LABEL_EGRESS]

    await box.destroy()

    leftovers = await asyncio.to_thread(_docker, "ps", "-aq", "--filter", f"label={LABEL_EGRESS}={token}")
    nets = await asyncio.to_thread(_docker, "network", "ls", "-q", "--filter", f"label={LABEL_EGRESS}={token}")
    assert leftovers == "" and nets == ""


@pytest.mark.usefixtures("outside")
async def test_an_adopted_allow_sandbox_wakes_its_gate(provider: LocalDockerProvider, made: list[Any]) -> None:
    box = await _sandbox(provider, made, ALLOW, **{"supervisor.session_id": "s-wake"})
    containers = (await asyncio.to_thread(_docker, "ps", "-aq", "--filter", f"label={LABEL_EGRESS}={box.info.labels[LABEL_EGRESS]}")).split()
    await asyncio.to_thread(_docker, "stop", "-t", "0", *containers)

    adopted = await provider.adopt("s-wake")

    assert adopted is not None and adopted.id == box.id, "the gate was adopted instead of the sandbox"
    assert (await _sh(adopted, f"{CURL}https://allowed.test/")).endswith("hello from outside 200")


@pytest.mark.usefixtures("outside")
async def test_the_browser_browses_through_the_gate(provider: LocalDockerProvider, made: list[Any]) -> None:
    box = await _sandbox(provider, made, ALLOW)
    ctx = ToolContext(bound_workspace=SandboxWorkspace(box), timeout_s=90)
    run = default_registry()["browser_exec"].run

    allowed = await run({"code": "page.goto('http://allowed.test/')\nprint(page.title())"}, ctx)
    denied = await run({"code": "page.goto('http://denied.test/', timeout=5000)"}, ctx)

    assert allowed["output"] == "outside", allowed
    assert "net::ERR" in denied["error"], denied


async def test_a_provider_that_cannot_enforce_a_network_refuses_it(tmp_path: Path) -> None:
    with pytest.raises(NetworkNotEnforceable):
        await LocalProcessProvider(base_dir=tmp_path).create(network=OPEN)
