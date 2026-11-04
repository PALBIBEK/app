"""What a sandbox may reach on the network (``SandboxProfile.network``), and who enforces it.

The provider enforces it, outside the sandbox, never the code inside: ``local_docker``
with Docker networks and an egress gate (``sandbox.egress``), ``e2b`` with E2B's own
egress rules. A provider that cannot enforce a mode refuses it, at publish and at create,
so a profile never runs looser than it reads.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from supervisor.catalog import NetworkAccess

__all__ = ["ENFORCED", "NetworkNotEnforceable", "check_network", "e2b_network", "mode_of"]

ENFORCED: Final[dict[str, frozenset[str]]] = {
    "local_docker": frozenset({"none", "open", "allow"}),
    "e2b": frozenset({"none", "open", "allow"}),
}
"""The network modes each provider enforces. Others (a host process, a tenant's runner)
enforce none of them: a profile there names no network."""

_HOST: Final = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*")


class NetworkNotEnforceable(ValueError):
    """The profile asks for a network its provider cannot enforce, or names it badly."""


def check_network(provider: str, network: NetworkAccess | None) -> None:
    """Raise ``NetworkNotEnforceable`` unless ``provider`` enforces ``network`` as written."""
    mode = mode_of(network)
    if network is None or mode is None:
        return
    if mode not in ENFORCED.get(provider, frozenset()):
        raise NetworkNotEnforceable(
            f"provider {provider!r} cannot enforce network {mode!r}"
            + (f": use one of {', '.join(sorted(ENFORCED))}" if provider not in ENFORCED else "")
        )
    if mode == "allow" and not network.hosts:
        raise NetworkNotEnforceable("network 'allow' needs hosts: the names the sandbox may reach")
    if mode != "allow" and network.hosts:
        raise NetworkNotEnforceable(f"hosts are for network 'allow', not {mode!r}")
    for host in network.hosts:
        if not _HOST.fullmatch(host):
            raise NetworkNotEnforceable(f"{host!r} is not a host name (no scheme, port, path or wildcard)")


def mode_of(network: NetworkAccess | None) -> str | None:
    """The mode as a plain string, or None for a profile that names no network."""
    return None if network is None else str(getattr(network.mode, "value", network.mode))


def e2b_network(network: NetworkAccess | None) -> dict[str, Any]:
    """E2B's create arguments for ``network``: its own egress rules enforce it (allowed
    entries win over denied ones, so the hosts and a deny-all make an allow-list)."""
    match mode_of(network):
        case None:
            return {}
        case "none":
            return {"allow_internet_access": False}
        case "open":
            return {"allow_internet_access": True}
        case _:
            assert network is not None
            return {"network": {"allow_out": list(network.hosts), "deny_out": ["0.0.0.0/0"]}}
