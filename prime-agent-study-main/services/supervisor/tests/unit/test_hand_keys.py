"""Hand keys, the ``hand`` argument, and what publish refuses about named hands."""

from __future__ import annotations

import dataclasses

import pytest

from supervisor.catalog.core.errors import InvalidArtifact
from supervisor.catalog.core.types import DigestRef
from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, default_catalog_dir
from supervisor.catalog.services.publish_checks import check_publishable
from supervisor.catalog.storage import artifact_to_dict, composition_from_dict
from supervisor.hands.keys import hand_key, split_key, take_hand, with_hand_argument

WRITE = {"type": "function", "function": {"name": "write", "parameters": {"type": "object", "properties": {"path": {}}}}}


def test_a_key_splits_back_to_its_session_and_hand() -> None:
    assert hand_key("s1") == hand_key("s1", "default") == "s1"
    assert split_key(hand_key("s1", "build")) == ("s1", "build")
    assert split_key("s1") == ("s1", "default")


def test_the_hand_argument_exists_only_with_named_hands() -> None:
    assert with_hand_argument(WRITE, ["default"]) is WRITE
    offered = with_hand_argument(WRITE, ["default", "build"])["function"]["parameters"]["properties"]
    assert offered["hand"]["enum"] == ["default", "build"] and "path" in offered
    assert "hand" not in WRITE["function"]["parameters"]["properties"]  # the shared schema is untouched
    assert take_hand({"path": "a", "hand": "build"}, ["default", "build"]) == ({"path": "a"}, "build")
    assert take_hand({"path": "a", "hand": "x"}, ["default"]) == ({"path": "a", "hand": "x"}, None)


def _githi() -> object:
    catalog = bootstrap_composition_catalog(default_catalog_dir())
    return catalog.get("composition", "githi-v1", catalog.list_versions("composition", "githi-v1")[-1])


def test_hands_round_trip_and_leave_other_digests_alone() -> None:
    base = _githi()
    ref = DigestRef(id="githi-docker-v1", digest="sha256:" + "a" * 64)
    with_hands = dataclasses.replace(base, hands=(("build", ref),))
    assert "hands" not in base.body_for_digest()  # type: ignore[attr-defined]
    assert composition_from_dict(artifact_to_dict(with_hands)).hands == (("build", ref),)


@pytest.mark.parametrize("name", ["default", "Build", "has space", "x" * 33])
def test_publish_refuses_a_hand_name_a_call_could_not_carry(name: str) -> None:
    base = _githi()
    ref = DigestRef(id="githi-docker-v1", digest="sha256:" + "a" * 64)
    comp = dataclasses.replace(base, hands=((name, ref),), sandbox_profile=ref)
    with pytest.raises(InvalidArtifact, match="hand name"):
        check_publishable("composition", artifact_to_dict(comp), comp)


def test_publish_refuses_named_hands_without_a_default() -> None:
    comp = dataclasses.replace(_githi(), hands=(("build", DigestRef(id="p", digest="sha256:" + "a" * 64)),), sandbox_profile=None)
    with pytest.raises(InvalidArtifact, match="default hand"):
        check_publishable("composition", artifact_to_dict(comp), comp)
