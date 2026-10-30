"""A local_docker sandbox runs boxed in: memory, CPU, processes, privileges, network.

It had no process limit (a fork bomb took every PID on the host) and could gain
privileges through a setuid binary in its image.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from supervisor.sandbox.providers.local_docker import DISK_LIMIT_ENV, LocalDockerProvider

IMAGE = "busybox@sha256:" + "0" * 64


class _Containers:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def run(self, image: str, **kwargs: Any) -> Any:
        self.runs.append(kwargs)
        return SimpleNamespace(id="c-1")


async def _created_with(monkeypatch: pytest.MonkeyPatch, disk: str | None) -> dict[str, Any]:
    if disk is None:
        monkeypatch.delenv(DISK_LIMIT_ENV, raising=False)
    else:
        monkeypatch.setenv(DISK_LIMIT_ENV, disk)
    client = SimpleNamespace(containers=_Containers())
    await LocalDockerProvider(client=client).create(image=IMAGE)
    (run,) = client.containers.runs
    return run


@pytest.mark.asyncio
async def test_a_sandbox_is_limited_in_processes_and_privileges(monkeypatch: pytest.MonkeyPatch) -> None:
    run = await _created_with(monkeypatch, disk=None)
    assert run["pids_limit"] == 512
    assert run["security_opt"] == ["no-new-privileges:true"]
    assert run["cap_drop"] == ["ALL"] and run["network_mode"] == "none"
    assert run["mem_limit"] == "512m" and run["nano_cpus"] == 1_000_000_000
    assert "storage_opt" not in run  # most hosts cannot enforce one: only when configured


@pytest.mark.asyncio
async def test_an_operator_can_cap_its_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    run = await _created_with(monkeypatch, disk="10G")
    assert run["storage_opt"] == {"size": "10G"}
