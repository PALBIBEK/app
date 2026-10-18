"""Reuse d6_acceptance fixtures for FULL-PRODUCTION BFF gates (separate package)."""

from __future__ import annotations

# Re-export shared helpers so this suite stays independent of MVP greens
# while avoiding fixture duplication.
from tests.d6_acceptance.conftest import (  # noqa: F401
    DIGEST,
    SECRET,
    as_user,
    d6_settings,
    make_harness,
    make_tool,
    other_user,
    owner,
    patch_ai_transport,
)
