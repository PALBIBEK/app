"""Harness subprocess spawn plan builder.

Builds CLI arguments and environment variables for harness subprocess
execution. Handles gateway routing from plug_bindings.json.

Reference: Original spawn.py refactored as service class.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from supervisor.harness.config import HarnessSettings, get_settings
from supervisor.harness.core.errors import (
    InvalidGatewayConfig,
    MissingHarnessCli,
)
from supervisor.harness.core.types import SpawnPlan

if TYPE_CHECKING:
    from supervisor.agent.seams import RuntimeSeams

__all__ = [
    "SpawnPlanner",
    "build_spawn_plan",
]

logger = logging.getLogger(__name__)


def _yal_dir(session_dir: str | Path) -> Path:
    """Get .yal directory path for session."""
    return Path(session_dir) / ".yal"


def _read_plug_bindings(session_dir: str | Path | None) -> dict[str, str]:
    """Read plug_bindings.json from session directory.

    Args:
        session_dir: Session directory path

    Returns:
        Dict of plug bindings or empty dict
    """
    if session_dir is None:
        return {}

    path = _yal_dir(session_dir) / "plug_bindings.json"
    if not path.is_file():
        return {}

    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items() if v}
    except Exception as exc:
        logger.warning(
            "Failed to read plug_bindings.json",
            extra={"path": str(path), "error": str(exc)},
        )
        return {}


def _read_launch_cli(session_dir: str | Path | None) -> str | None:
    """Read CLI path from launch.json.

    Args:
        session_dir: Session directory path

    Returns:
        CLI path or None
    """
    if session_dir is None:
        return None

    path = _yal_dir(session_dir) / "launch.json"
    if not path.is_file():
        return None

    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cli = data.get("cli") if isinstance(data, dict) else None
        return str(cli).strip() if cli else None
    except Exception as exc:
        logger.warning(
            "Failed to read launch.json",
            extra={"path": str(path), "error": str(exc)},
        )
        return None


def _session_vk_env(session_dir: str | None) -> dict[str, str]:
    """Build environment variables for session VK token.

    Args:
        session_dir: Session directory path

    Returns:
        Dict of environment variables
    """
    if not session_dir:
        return {}

    vk_path = _yal_dir(session_dir) / "vk"
    if not vk_path.is_file():
        return {}

    try:
        token = vk_path.read_text(encoding="utf-8").strip()
    except Exception:
        return {}

    bind = str(vk_path)
    out: dict[str, str] = {
        "YAL_VK_BIND_PATH": bind,
        "OPENAI_API_KEY_FILE": bind,
    }

    if token.startswith("vk_"):
        out["OPENAI_API_KEY"] = token

    return out


class SpawnPlanner:
    """Service for building harness subprocess spawn plans.

    Builds CLI arguments and environment variables based on
    session configuration and plug bindings.
    """

    def __init__(
        self,
        *,
        settings: HarnessSettings | None = None,
    ) -> None:
        """Initialize planner.

        Args:
            settings: Settings instance (uses get_settings() if None)
        """
        self._settings = settings or get_settings()
        self._logger = logging.getLogger(__name__)

    def resolve_cli(self, session_dir: str | None) -> str:
        """Resolve harness CLI path.

        Checks launch.json first, then HARNESS_CLI environment variable.

        Args:
            session_dir: Session directory path

        Returns:
            CLI path

        Raises:
            MissingHarnessCli: If no CLI configured
        """
        # Try launch.json first
        cli = _read_launch_cli(session_dir)
        if cli:
            return cli

        # Try settings
        if self._settings.harness_cli:
            return self._settings.harness_cli

        raise MissingHarnessCli()

    def build(
        self,
        *,
        session_dir: str | None,
        model: str | None,
        provider: str | None,
        seams: RuntimeSeams | None,
    ) -> SpawnPlan:
        """Build spawn plan for harness subprocess.

        Args:
            session_dir: Session directory path
            model: Model name override
            provider: Provider name override
            seams: Runtime seams for gateway binding

        Returns:
            SpawnPlan with argv and env

        Raises:
            MissingHarnessCli: If no CLI configured
            InvalidGatewayConfig: If gateway config is invalid
        """
        cli = self.resolve_cli(session_dir)
        plug = _read_plug_bindings(session_dir)
        gateway_url = plug.get("gateway_base_url", "").strip()
        model_route = plug.get("model_route") or model

        if gateway_url:
            # Gateway mode - route through OpenAI-compatible gateway
            gateway = seams.model_gateway if seams is not None else None

            if gateway is None:
                raise InvalidGatewayConfig(
                    "plug_bindings.gateway_base_url requires RuntimeSeams.model_gateway"
                )

            cls_name = gateway.__class__.__name__

            if cls_name == "FakeModelGatewayClient":
                raise InvalidGatewayConfig(
                    "plug_bindings.gateway_base_url refuses FakeModelGatewayClient"
                )

            if cls_name not in ("HttpModelGatewayClient", "RoutedModelClient"):
                raise InvalidGatewayConfig(
                    "plug_bindings.gateway_base_url requires an HTTP model gateway client"
                )

            base = gateway_url.rstrip("/")
            if not base.endswith("/v1"):
                base = f"{base}/v1"

            env = {
                "OPENAI_BASE_URL": base,
                **_session_vk_env(session_dir),
            }

            provider_name = "openai"
            default_model = getattr(gateway, "_default_model", None)
            model_name = model_route or default_model or "default"

            self._logger.debug(
                "Building gateway-mode spawn plan",
                extra={
                    "gateway_url": gateway_url,
                    "model": model_name,
                },
            )
        else:
            # Direct mode - use provider/model directly
            env = {}
            provider_name = provider or "openrouter"
            model_name = model or "anthropic/claude-sonnet-4"

            self._logger.debug(
                "Building direct-mode spawn plan",
                extra={
                    "provider": provider_name,
                    "model": model_name,
                },
            )

        argv = (
            cli,
            "--mode",
            "rpc",
            "--provider",
            provider_name,
            "--model",
            model_name,
            "--no-session",
        )

        return SpawnPlan(argv=argv, env=env)


# Module-level convenience function for backward compatibility
def build_spawn_plan(
    *,
    session_dir: str | None,
    model: str | None,
    provider: str | None,
    seams: RuntimeSeams | None,
) -> SpawnPlan:
    """Build spawn plan (convenience function).

    Args:
        session_dir: Session directory path
        model: Model name override
        provider: Provider name override
        seams: Runtime seams for gateway binding

    Returns:
        SpawnPlan with argv and env

    Raises:
        MissingHarnessCli: If no CLI configured
        InvalidGatewayConfig: If gateway config is invalid
    """
    planner = SpawnPlanner()
    return planner.build(
        session_dir=session_dir,
        model=model,
        provider=provider,
        seams=seams,
    )
