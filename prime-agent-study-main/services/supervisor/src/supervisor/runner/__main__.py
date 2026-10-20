"""The YAL runner: hosts a tenant's hands on this machine, dialling out to the supervisor.

    python -m supervisor.runner --url https://supervisor.example.com \\
        --tenant acme --name office --token yr_... --root /srv/yal-hands

Register the runner first (``POST /v1/runners/{name}``, which returns its token
once). The runner opens no port: it long-polls the supervisor over HTTPS for the
next command, runs it in one directory per hand under ``--root``, and posts the
result. Commands of different hands run concurrently (``--concurrency``).

What runs here is whatever the tenant's agents ask their tools to run: give the
runner a machine (or a container) and an account meant for exactly that.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import signal
from pathlib import Path
from typing import Any

import httpx

from supervisor.sandbox.providers.local_process import LocalProcessSandbox

logger = logging.getLogger("supervisor.runner")

_BACKOFF_MAX_S = 30.0


class Runner:
    def __init__(
        self, *, url: str, tenant: str, name: str, token: str, root: Path, concurrency: int = 8
    ) -> None:
        self._name = name
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant},
            timeout=httpx.Timeout(75.0, connect=10.0),
        )
        self._slots = asyncio.Semaphore(concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    async def serve(self, stop: asyncio.Event) -> None:
        """Poll for commands until ``stop`` is set; a lost connection is retried with backoff."""
        delay = 1.0
        logger.info("runner %s serving hands under %s", self._name, self._root)
        while not stop.is_set():
            try:
                await self._slots.acquire()
                command = await self._next()
            except httpx.HTTPError as exc:
                self._slots.release()
                logger.warning("supervisor unreachable (%s); retrying in %.0fs", exc, delay)
                await _sleep_or_stop(stop, delay)
                delay = min(delay * 2, _BACKOFF_MAX_S)
                continue
            delay = 1.0
            if command is None:
                self._slots.release()
                continue
            task = asyncio.create_task(self._run(command))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        for task in list(self._tasks):
            await task
        await self._client.aclose()

    async def _next(self) -> dict[str, Any] | None:
        resp = await self._client.get(f"/v1/runners/{self._name}/next", params={"wait": 20})
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return dict(resp.json())

    async def _run(self, command: dict[str, Any]) -> None:
        try:
            try:
                body: dict[str, Any] = {"ok": True, "result": await self.execute(command)}
            except Exception as exc:  # noqa: BLE001 — the supervisor gets the failure, by kind
                body = {"ok": False, "result": {"kind": type(exc).__name__, "error": str(exc)}}
            for attempt in range(5):  # the result must arrive; the call waiting on it times out
                try:
                    await self._client.post(f"/v1/runners/{self._name}/commands/{command['id']}", json=body)
                    return
                except httpx.HTTPError:
                    await asyncio.sleep(min(2**attempt, 10))
        finally:
            self._slots.release()

    def _hand(self, sandbox_id: str | None) -> LocalProcessSandbox:
        if not sandbox_id or "/" in sandbox_id or sandbox_id.startswith("."):
            raise ValueError(f"bad sandbox id {sandbox_id!r}")
        return LocalProcessSandbox(self._root / sandbox_id, sandbox_id)

    async def execute(self, command: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command on this machine; returns its result."""
        op, args = command["op"], dict(command.get("args") or {})
        sandbox_id = command.get("sandbox_id")
        if op == "create":
            (self._root / str(sandbox_id)).mkdir(parents=True, exist_ok=True)  # kept if it exists
            self._hand(sandbox_id)
            return {}
        if not (self._root / str(sandbox_id)).is_dir():
            raise FileNotFoundError(f"no hand {sandbox_id!r} on this runner")
        hand = self._hand(sandbox_id)
        if op == "exec":
            result = await hand.exec(
                [str(c) for c in args["cmd"]], cwd=args.get("cwd"), env=args.get("env") or None, timeout=args.get("timeout")
            )
            return {
                "exit_code": result.exit_code,
                "stdout": base64.b64encode(result.stdout).decode(),
                "stderr": base64.b64encode(result.stderr).decode(),
                "duration_ms": result.duration_ms,
            }
        if op == "read":
            return {"data": base64.b64encode(await hand.read_file(str(args["path"]))).decode()}
        if op == "write":
            await hand.write_file(str(args["path"]), base64.b64decode(args["data"]))
            return {}
        if op == "exists":
            return {"exists": await hand.exists(str(args["path"]))}
        tree = hand.as_tree_io()
        if op == "list":
            return {"paths": await tree.list_paths()}
        if op == "delete":
            await tree.delete_path(str(args["path"]))
            return {}
        if op == "clear":
            await tree.clear()
            return {}
        if op == "destroy":
            await hand.destroy()
            return {}
        raise ValueError(f"unknown op {op!r}")


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m supervisor.runner", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=os.environ.get("YAL_SUPERVISOR_URL"), required="YAL_SUPERVISOR_URL" not in os.environ)
    parser.add_argument("--tenant", default=os.environ.get("YAL_TENANT"), required="YAL_TENANT" not in os.environ)
    parser.add_argument("--name", default=os.environ.get("YAL_RUNNER"), required="YAL_RUNNER" not in os.environ)
    parser.add_argument("--token", default=os.environ.get("YAL_RUNNER_TOKEN"), required="YAL_RUNNER_TOKEN" not in os.environ)
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("YAL_RUNNER_ROOT", "./yal-hands")))
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    runner = Runner(
        url=args.url, tenant=args.tenant, name=args.name, token=args.token, root=args.root.resolve(),
        concurrency=args.concurrency,
    )

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await runner.serve(stop)

    asyncio.run(run())


if __name__ == "__main__":
    main()
