"""Shared helpers for D14 blind acceptance — real local sandbox image pins."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

import yaml

from supervisor.catalog.store import sandbox_profile_from_dict
from supervisor.catalog.types import SandboxProfile
from supervisor.harness import HarnessManifest, load_manifest

AI_ROOT = Path(__file__).resolve().parents[4]
HARNESS_ROOT = AI_ROOT / "harness"
CODING_DOCKER_PROFILE = HARNESS_ROOT / "sandbox_profiles" / "coding-docker-v1.json"
CODING_V1_YAML = HARNESS_ROOT / "versions" / "coding-v1.yaml"

_SHA256_HEX = re.compile(r"^sha256:([0-9a-f]{64})$")

# D14 §4 A / §5 T1–T5 — fixture digests that must never ship for coding pins.
_PLACEHOLDER_MARKERS = ("REPLACE", "PLACEHOLDER")


def is_acceptance_placeholder_digest(digest: str | None) -> bool:
    """True when digest is a known fixture / placeholder (aaaa…, bbbb…, REPLACE, …).

    Acceptance definition from D14.md — independent of product helpers.
    """
    if not digest:
        return True
    d = digest.strip()
    upper = d.upper()
    for marker in _PLACEHOLDER_MARKERS:
        if marker in upper:
            return True
    m = _SHA256_HEX.match(d)
    if not m:
        return False
    hex_part = m.group(1)
    if len(set(hex_part)) == 1:
        # all-a, all-b, all-0, etc. — fixture pins, not built images
        return True
    return False


def assert_real_image_digest(digest: str | None, *, label: str) -> str:
    """Fail unless digest is sha256:<64 hex> and not a D14 placeholder."""
    assert digest, f"{label}: image_digest missing"
    assert _SHA256_HEX.match(digest), (
        f"{label}: image_digest must be sha256:<64 lowercase hex>, got {digest!r}"
    )
    assert not is_acceptance_placeholder_digest(digest), (
        f"{label}: image_digest is still a placeholder fixture: {digest!r}. "
        "Build harness/images/Dockerfile and re-pin a real image id (D14)."
    )
    return digest


def load_coding_docker_profile_raw() -> dict[str, Any]:
    assert CODING_DOCKER_PROFILE.is_file(), f"missing profile {CODING_DOCKER_PROFILE}"
    return json.loads(CODING_DOCKER_PROFILE.read_text(encoding="utf-8"))


def load_coding_docker_profile() -> SandboxProfile:
    return sandbox_profile_from_dict(load_coding_docker_profile_raw())


def load_coding_v1_yaml_raw() -> dict[str, Any]:
    assert CODING_V1_YAML.is_file(), f"missing harness version {CODING_V1_YAML}"
    data = yaml.safe_load(CODING_V1_YAML.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"coding-v1.yaml must be a mapping, got {type(data)}"
    return data


def load_coding_v1_manifest() -> HarnessManifest:
    return load_manifest(CODING_V1_YAML.read_text(encoding="utf-8"))


def docker_cli_available() -> bool:
    return shutil.which("docker") is not None


def docker_image_inspect(digest: str) -> subprocess.CompletedProcess[str]:
    """Inspect image id on the local daemon (same sock supervisor would use)."""
    return subprocess.run(
        ["docker", "image", "inspect", digest],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def collect_product_placeholder_detectors() -> list[tuple[str, Callable[[str], bool]]]:
    """Discover product-side placeholder guards (catalog helper and/or D14 guard)."""
    found: list[tuple[str, Callable[[str], bool]]] = []

    try:
        from supervisor.sandbox.d14_guard import is_placeholder_image_digest as fn

        found.append(("supervisor.sandbox.d14_guard.is_placeholder_image_digest", fn))
    except ImportError:
        pass

    try:
        from supervisor.sandbox.image_pin import is_placeholder_image_digest as fn

        found.append(("supervisor.sandbox.image_pin.is_placeholder_image_digest", fn))
    except ImportError:
        pass

    try:
        from supervisor.catalog.loader import _is_placeholder_digest as fn

        found.append(("supervisor.catalog.loader._is_placeholder_digest", fn))
    except ImportError:
        pass

    return found
