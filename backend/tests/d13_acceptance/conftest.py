"""Reuse d6 fixtures for D13 BFF traceparent + Trace API gates."""

from __future__ import annotations

from tests.d6_acceptance.conftest import (  # noqa: F401
    DIGEST,
    SECRET,
    as_user,
    d6_settings,
    make_harness,
    other_user,
    owner,
    patch_ai_transport,
)
