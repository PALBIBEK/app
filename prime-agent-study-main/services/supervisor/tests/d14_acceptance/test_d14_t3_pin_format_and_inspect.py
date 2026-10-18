"""D14 T3 — pin format is real sha256 + not placeholder; optional docker inspect."""

from __future__ import annotations

import pytest

from tests.d14_acceptance.conftest import (
    assert_real_image_digest,
    docker_cli_available,
    docker_image_inspect,
    is_acceptance_placeholder_digest,
    load_coding_docker_profile_raw,
)


def test_t3_pin_is_sha256_hex_and_not_placeholder() -> None:
    """T3: prefer format + honesty without requiring docker on every laptop."""
    digest = load_coding_docker_profile_raw().get("image_digest")
    assert_real_image_digest(digest, label="coding-docker-v1.json")


def test_t3_fails_while_placeholder_still_pinned() -> None:
    """Separate T3 honesty row: explicit fail if fixture digest remains shipped."""
    digest = load_coding_docker_profile_raw().get("image_digest")
    assert digest, "coding-docker-v1.json missing image_digest"
    assert not is_acceptance_placeholder_digest(digest), (
        f"placeholder image_digest still pinned in coding-docker-v1.json: {digest!r}"
    )


@pytest.mark.skipif(not docker_cli_available(), reason="docker CLI not available")
def test_t3_docker_inspect_finds_pinned_image_when_daemon_present() -> None:
    """Optional: when docker is present, daemon must know the pinned id."""
    digest = assert_real_image_digest(
        load_coding_docker_profile_raw().get("image_digest"),
        label="coding-docker-v1.json",
    )
    result = docker_image_inspect(digest)
    assert result.returncode == 0, (
        f"docker image inspect failed for pinned digest {digest!r}:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
