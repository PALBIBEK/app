"""The networks of ``local_docker`` sandboxes, by their profile's ``network``.

* ``none`` (no profile network either): no network at all (``network_mode: none``).
* ``open``: ``githi-sandbox-open``, a bridge network whose containers cannot reach each
  other (``enable_icc`` off): out to anywhere, never across to another sandbox.
* ``allow``: a network of the sandbox's own with no route out (``internal``), shared only
  with its egress gate (``supervisor.sandbox.egress_gate``), which is on the open network
  too; each allowed host resolves, in the sandbox, to the gate, which lets through only
  connections for those hosts.

The sandbox and its gate carry one token (``LABEL_EGRESS``), which also names the
sandbox's network: whatever destroys or wakes the sandbox finds them by it.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING, Any, Final

from supervisor.sandbox.network import mode_of

if TYPE_CHECKING:
    from supervisor.catalog import NetworkAccess

__all__ = ["LABEL_EGRESS", "LABEL_NETWORK", "OPEN_NETWORK", "DockerNetworks", "NetworkPlan"]

OPEN_NETWORK: Final = "githi-sandbox-open"
LABEL_NETWORK: Final = "supervisor.network"
LABEL_EGRESS: Final = "supervisor.egress"
LABEL_ROLE: Final = "supervisor.role"
GATE_ROLE: Final = "egress-gate"
GATE_LIMITS: Final[dict[str, Any]] = {
    "mem_limit": "64m",
    "nano_cpus": 250_000_000,
    "pids_limit": 64,
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "read_only": True,
    "sysctls": {"net.ipv4.ip_unprivileged_port_start": "0"},  # 80 and 443 without a capability
}
"""The gate holds no secret and runs only the gate, but is hardened as a sandbox is."""


@dataclass(frozen=True, slots=True)
class NetworkPlan:
    """What the sandbox's ``containers.run`` takes for its network, and its labels."""

    run: dict[str, Any]
    labels: dict[str, str] = field(default_factory=dict)


class DockerNetworks:
    """Makes, wakes and removes what a sandbox's network needs."""

    def __init__(self, client: Any, *, gate_hosts: Mapping[str, str] | None = None) -> None:
        self._client = client
        self._gate_hosts = dict(gate_hosts or {})
        """Names the gate resolves itself (``extra_hosts``), before DNS: a test's stand-in hosts."""

    def plan(self, network: NetworkAccess | None, image: str) -> NetworkPlan:
        """The sandbox's network, made ready (an open network, or a gate) before it starts."""
        mode = mode_of(network) or "none"
        if mode == "none":
            return NetworkPlan(run={"network_mode": "none"}, labels={LABEL_NETWORK: "none"})
        self._open_network()
        if mode == "open":
            return NetworkPlan(run={"network": OPEN_NETWORK}, labels={LABEL_NETWORK: "open"})
        assert network is not None
        token = secrets.token_hex(6)
        gate_ip = self._gate(token, image, network.hosts)
        return NetworkPlan(
            run={"network": _own_network(token), "extra_hosts": {host: gate_ip for host in network.hosts}},
            labels={LABEL_NETWORK: "allow", LABEL_EGRESS: token},
        )

    def _open_network(self) -> None:
        try:
            self._client.networks.get(OPEN_NETWORK)
        except Exception:  # noqa: BLE001 — not made yet (or made by a racing worker: then this fails, the get worked)
            try:
                self._client.networks.create(
                    OPEN_NETWORK, driver="bridge", options={"com.docker.network.bridge.enable_icc": "false"}
                )
            except Exception:  # noqa: BLE001 — another worker made it first
                self._client.networks.get(OPEN_NETWORK)

    def _gate(self, token: str, image: str, hosts: tuple[str, ...]) -> str:
        """Start the sandbox's egress gate on its own network; the gate's address there."""
        own = self._client.networks.create(_own_network(token), driver="bridge", internal=True, labels={LABEL_EGRESS: token})
        source = resources.files("supervisor.sandbox").joinpath("egress_gate.py").read_text()
        gate = self._client.containers.create(
            image,
            command=["python3", "-c", source, *hosts],
            detach=True,
            network=OPEN_NETWORK,
            extra_hosts=self._gate_hosts or None,
            labels={LABEL_EGRESS: token, LABEL_ROLE: GATE_ROLE},  # never the session's: adoption must not find it
            **GATE_LIMITS,
        )
        own.connect(gate)
        gate.start()
        gate.reload()
        return str(gate.attrs["NetworkSettings"]["Networks"][_own_network(token)]["IPAddress"])

    def wake(self, labels: Mapping[str, str]) -> None:
        """Start a stopped sandbox's gate again (an adopted sandbox wakes with it)."""
        for gate in self._gates(labels):
            if getattr(gate, "status", "running") != "running":
                gate.start()

    def release(self, labels: Mapping[str, str]) -> None:
        """Remove the sandbox's gate and network, once the sandbox itself is gone."""
        for gate in self._gates(labels):
            gate.remove(force=True)
        if token := labels.get(LABEL_EGRESS):
            for net in self._client.networks.list(filters={"label": f"{LABEL_EGRESS}={token}"}):
                net.remove()

    def _gates(self, labels: Mapping[str, str]) -> list[Any]:
        token = labels.get(LABEL_EGRESS)
        if not token:
            return []
        found = self._client.containers.list(all=True, filters={"label": [f"{LABEL_EGRESS}={token}", f"{LABEL_ROLE}={GATE_ROLE}"]})
        return list(found)


def _own_network(token: str) -> str:
    return f"githi-egress-{token}"
