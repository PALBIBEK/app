"""D14 T2 — coding-v1.yaml image_digest matches profile (same real digest)."""

from __future__ import annotations

from tests.d14_acceptance.conftest import (
    assert_real_image_digest,
    load_coding_docker_profile_raw,
    load_coding_v1_yaml_raw,
)


def test_t2_coding_v1_yaml_image_digest_matches_profile() -> None:
    """T2: yaml and profile pins must agree on one real image id."""
    profile_digest = load_coding_docker_profile_raw().get("image_digest")
    yaml_digest = load_coding_v1_yaml_raw().get("image_digest")

    assert profile_digest, "coding-docker-v1.json missing image_digest"
    assert yaml_digest, "coding-v1.yaml missing image_digest"
    assert yaml_digest == profile_digest, (
        "coding-v1.yaml image_digest must match coding-docker-v1.json "
        f"(yaml={yaml_digest!r} profile={profile_digest!r})"
    )
    # Matching placeholders is not PASS — both must be a real built image id.
    assert_real_image_digest(yaml_digest, label="coding-v1.yaml (aligned pin)")
