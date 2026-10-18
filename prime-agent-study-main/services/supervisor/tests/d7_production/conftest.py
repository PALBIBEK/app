"""Shared helpers for independent D7 FULL-PRODUCTION supervisor/deploy acceptance."""

from __future__ import annotations

from pathlib import Path

AI_REPO = Path(__file__).resolve().parents[4]
ROOT_COMPOSE = AI_REPO / "docker-compose.yml"
STAGING_COMPOSE = AI_REPO / "deploy" / "staging" / "docker-compose.staging.yml"
# Explicit documented marker accepted only when root compose intentionally omits memory.
STAGING_ONLY_MARKER = AI_REPO / "deploy" / "STAGING_ONLY_MEMORY.md"
GATEWAY_PORT = 8090
