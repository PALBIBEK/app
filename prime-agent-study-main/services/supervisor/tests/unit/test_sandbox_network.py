"""A sandbox profile's network: what each provider enforces, how it is named, and how the
catalog refuses a profile that would run looser than it reads."""

from __future__ import annotations

from typing import Any

import pytest

from supervisor.catalog import NetworkAccess, NetworkMode
from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.services.publish_checks import check_publishable
from supervisor.catalog.storage import artifact_from_dict
from supervisor.sandbox.network import NetworkNotEnforceable, check_network, e2b_network

ALLOW = NetworkAccess(NetworkMode.ALLOW, ("connect.browserbase.com",))


@pytest.mark.parametrize("provider", ["local_docker", "e2b"])
@pytest.mark.parametrize("network", [None, NetworkAccess(NetworkMode.NONE), NetworkAccess(NetworkMode.OPEN), ALLOW])
def test_docker_and_e2b_enforce_every_mode(provider: str, network: NetworkAccess | None) -> None:
    check_network(provider, network)


@pytest.mark.parametrize("provider", ["local_process", "runner", "none"])
def test_providers_that_isolate_no_network_refuse_any(provider: str) -> None:
    check_network(provider, None)
    for network in (NetworkAccess(NetworkMode.NONE), NetworkAccess(NetworkMode.OPEN), ALLOW):
        with pytest.raises(NetworkNotEnforceable, match="cannot enforce"):
            check_network(provider, network)


@pytest.mark.parametrize(
    ("network", "why"),
    [
        (NetworkAccess(NetworkMode.ALLOW), "needs hosts"),
        (NetworkAccess(NetworkMode.OPEN, ("a.example",)), "hosts are for network 'allow'"),
        (NetworkAccess(NetworkMode.ALLOW, ("https://a.example",)), "not a host name"),
        (NetworkAccess(NetworkMode.ALLOW, ("a.example:443",)), "not a host name"),
        (NetworkAccess(NetworkMode.ALLOW, ("*.example.com",)), "not a host name"),
    ],
)
def test_a_badly_named_network_is_refused(network: NetworkAccess, why: str) -> None:
    with pytest.raises(NetworkNotEnforceable, match=why):
        check_network("local_docker", network)


def test_e2b_enforces_each_mode_with_its_own_egress_rules() -> None:
    assert e2b_network(None) == {}
    assert e2b_network(NetworkAccess(NetworkMode.NONE)) == {"allow_internet_access": False}
    assert e2b_network(NetworkAccess(NetworkMode.OPEN)) == {"allow_internet_access": True}
    assert e2b_network(ALLOW) == {"network": {"allow_out": ["connect.browserbase.com"], "deny_out": ["0.0.0.0/0"]}}


def _profile(**fields: Any) -> dict[str, Any]:
    return {"name": "p", "semver": "1.0.0", "provider": "local_docker", "image_digest": "img", **fields}


def _publish(body: dict[str, Any]) -> None:
    check_publishable("sandbox_profile", body, artifact_from_dict("sandbox_profile", body))


def test_a_profile_publishes_with_a_network_its_provider_enforces() -> None:
    _publish(_profile(network={"mode": "allow", "hosts": ["connect.browserbase.com"]}, browser="remote-cdp"))
    _publish(_profile(network={"mode": "open"}, browser="sandbox-chromium"))


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"provider": "local_process", "network": {"mode": "open"}}, "cannot enforce network 'open'"),
        ({"browser": "remote-cdp"}, "needs network allow or open"),
        ({"browser": "remote-cdp", "network": {"mode": "none"}}, "needs network allow or open"),
        ({"browser": "a-browser-we-do-not-have"}, "use one of browser-use, browserbase, firecrawl, remote-cdp, sandbox-chromium, sandbox-lightpanda"),
    ],
)
def test_a_profile_that_would_not_work_as_written_is_refused(fields: dict[str, Any], why: str) -> None:
    with pytest.raises(InvalidArtifact, match=why):
        _publish(_profile(**fields))


def test_a_profile_without_a_network_keeps_its_digest() -> None:
    """Shipped profiles name no network: adding the field changed none of their digests."""
    body = artifact_from_dict("sandbox_profile", _profile()).body_for_digest()
    assert "network" not in body and "browser" not in body
