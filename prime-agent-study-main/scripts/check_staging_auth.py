#!/usr/bin/env python3
"""CI gate: staging + root compose must not ship empty AI auth defaults (D1/D7)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
ROOT_COMPOSE = ROOT / "docker-compose.yml"

REQUIRED_STAGING_KEYS = (
    "ENV",
    "AUTH_REQUIRED",
    "SERVICE_JWT_SECRET",
    "MODEL_GATEWAY_TOKEN",
    "MEMORY_SERVICE_URL",
    "memory:",
)


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}", file=sys.stderr)
    return 1


def check_staging() -> int:
    text = STAGING.read_text(encoding="utf-8")
    missing = [k for k in REQUIRED_STAGING_KEYS if k not in text]
    if missing:
        return _fail(f"staging compose missing keys: {missing}")

    for key in ("SERVICE_JWT_SECRET", "MODEL_GATEWAY_TOKEN"):
        m = re.search(rf"{key}\s*:\s*(.+)", text)
        if not m:
            return _fail(f"{key} not set in staging")
        val = m.group(1).strip().strip('"').strip("'")
        if val in ("", '""') or re.fullmatch(r"\$\{[^}]+:-\}", val):
            return _fail(f"{key} empty default: {val!r}")

    if "ENV: staging" not in text and 'ENV: "staging"' not in text:
        return _fail("ENV: staging required on staging services")

    # JWT-first: static MEMORY_ALLOW_STATIC_TOKEN must not be enabled in staging.
    if re.search(
        r"(?m)^\s*MEMORY_ALLOW_STATIC_TOKEN:\s*[\"']?(1|true|yes|on)[\"']?\s*$",
        text,
        re.I,
    ):
        return _fail(
            "staging must not enable MEMORY_ALLOW_STATIC_TOKEN "
            "(JWT-first Memory auth; CI residual only)"
        )
    return 0


def check_root_compose() -> int:
    """Root compose must not set MODEL_GATEWAY_TOKEN to empty string; must include memory."""
    text = ROOT_COMPOSE.read_text(encoding="utf-8")
    if re.search(r"MODEL_GATEWAY_TOKEN:\s*(\"\"|'')\s*$", text, re.M):
        return _fail(
            "root docker-compose.yml ships empty MODEL_GATEWAY_TOKEN; "
            "use a non-empty default or PROFILE=dev with an explicit override"
        )
    if "PROFILE:" not in text and "PROFILE=" not in text:
        return _fail("root compose must declare PROFILE (e.g. PROFILE: ${PROFILE:-dev})")
    if not re.search(r"(?m)^\s*memory(-service)?:\s*$", text):
        return _fail("root docker-compose.yml missing memory service (D7)")
    if "MEMORY_SERVICE_URL" not in text:
        return _fail("root compose supervisor must set MEMORY_SERVICE_URL")
    if "8092" not in text:
        return _fail("root compose memory must use non-colliding port 8092")
    return 0


def main() -> int:
    for checker in (check_staging, check_root_compose):
        code = checker()
        if code != 0:
            return code
    print("OK: staging + root AI auth defaults present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
