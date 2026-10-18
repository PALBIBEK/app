"""D14 T1 — shipped coding-docker-v1.json image_digest is not a placeholder."""

from __future__ import annotations

from tests.d14_acceptance.conftest import (
    assert_real_image_digest,
    load_coding_docker_profile_raw,
)


def test_t1_coding_docker_v1_image_digest_not_placeholder() -> None:
    """T1: profile pin must be a real digest — FAIL while aaaa…/bbbb…/REPLACE ship."""
    raw = load_coding_docker_profile_raw()
    digest = raw.get("image_digest")
    assert_real_image_digest(digest, label="coding-docker-v1.json")
