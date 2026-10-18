"""D14 T4 — resolve path for coding pins must not hand local_docker a placeholder."""

from __future__ import annotations

from supervisor.sandbox.local_docker import LocalDockerProvider
from supervisor.sandbox.resolve import resolve_sandbox_create

from tests.d14_acceptance.conftest import (
    assert_real_image_digest,
    is_acceptance_placeholder_digest,
    load_coding_docker_profile,
    load_coding_v1_manifest,
)
from tests.unit.test_docker import FakeDockerClient


def test_t4_resolve_coding_pins_image_not_placeholder_for_local_docker() -> None:
    """T4 (suite): shipped pins + resolve must not yield a placeholder image.

    Live ``POST /v1/sessions`` → 201 with a real container is still required for
    D14 VERIFY (see README). This row fails closed when resolve would pass
    aaaa…/bbbb…/REPLACE into the local_docker create path.

    Uses FakeDockerClient so LocalDockerProvider constructs without the optional
    ``docker`` package / daemon — the assert under test is the image pin.
    """
    manifest = load_coding_v1_manifest()
    profile = load_coding_docker_profile()

    assert profile.provider == "local_docker", (
        f"coding-docker-v1 must use local_docker, got {profile.provider!r}"
    )
    assert manifest.requires_sandbox is True

    # Fail closed on the pin before / as well as after resolve (honest signal).
    pin_image = profile.image_digest or manifest.image_digest
    assert pin_image, "coding pins missing image_digest on profile and manifest"
    assert not is_acceptance_placeholder_digest(pin_image), (
        f"local_docker resolve would use placeholder image: {pin_image!r}. "
        "Re-pin coding-docker-v1 / coding-v1 before live session create can 201."
    )

    provider, image = resolve_sandbox_create(
        manifest,
        deploy_provider=None,
        profile=profile,
        client=FakeDockerClient(),
    )

    assert provider is not None, "coding-v1 + coding-docker-v1 must resolve a provider"
    assert isinstance(provider, LocalDockerProvider), (
        f"expected LocalDockerProvider for coding pins, got {type(provider).__name__}"
    )
    assert image == pin_image
    assert_real_image_digest(image, label="resolve_sandbox_create(coding pins)")
