"""D14 T5 — product placeholder detection / d14 guard covers aaaa… fixtures."""

from __future__ import annotations

from tests.d14_acceptance.conftest import (
    collect_product_placeholder_detectors,
    is_acceptance_placeholder_digest,
    load_coding_docker_profile_raw,
)


def test_t5_product_detector_covers_aaaa_and_bbbb_placeholders() -> None:
    """T5: catalog helper or d14 guard must treat aaaa…/bbbb… as placeholders.

    Today's ``loader._is_placeholder_digest`` only matches REPLACE/PLACEHOLDER —
    that is insufficient for D14 honesty. Implement a guard that covers fixture
    hex pins, or extend catalog detection; do not soft-fallback to local_process.
    """
    detectors = collect_product_placeholder_detectors()
    assert detectors, (
        "D14 requires a product placeholder detector "
        "(supervisor.sandbox.d14_guard / image_pin, or catalog.loader helper)"
    )

    aaaa = "sha256:" + "a" * 64
    bbbb = "sha256:" + "b" * 64
    replace = "sha256:REPLACE_AFTER_OFFER"
    place = "sha256:PLACEHOLDER"

    covered_aaaa = False
    for name, fn in detectors:
        assert fn(replace) or "REPLACE" in name, (
            f"{name} should reject REPLACE-style composition placeholders"
        )
        if fn(aaaa):
            covered_aaaa = True
            assert fn(bbbb), f"{name} must also treat bbbb… as placeholder"
            assert fn(place) or fn(replace), (
                f"{name} should also cover PLACEHOLDER/REPLACE markers"
            )

    assert covered_aaaa, (
        "No product detector treats sha256:aaaa… as a placeholder. "
        f"Tried: {[n for n, _ in detectors]}. "
        "Add a d14 guard or extend catalog placeholder detection (D14 T5)."
    )


def test_t5_shipped_coding_pin_rejected_by_product_or_acceptance() -> None:
    """T5 companion: while a fixture pin ships, product detectors must reject it."""
    digest = load_coding_docker_profile_raw().get("image_digest")
    assert digest, "coding-docker-v1.json missing image_digest"

    if not is_acceptance_placeholder_digest(digest):
        # Real pin after re-pin — shipped artifact no longer needs rejection.
        return

    detectors = collect_product_placeholder_detectors()
    assert detectors, "missing product placeholder detector (see T5 primary test)"
    assert any(fn(digest) for _, fn in detectors), (
        f"shipped coding pin {digest!r} is a fixture placeholder but no product "
        "detector rejects it — D14 T5 FAIL"
    )
