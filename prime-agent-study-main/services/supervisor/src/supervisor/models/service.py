"""The process's model router, built once from the gateway environment."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

from supervisor.models.client import ModelRouter
from supervisor.models.endpoints import GatewayConfig

_lock = threading.Lock()
_router: ModelRouter | None = None
_loaded = False


def model_router(env: Mapping[str, str] | None = None) -> ModelRouter | None:
    """The configured router, or None when no ``MODEL_GATEWAY_*`` variable is set.

    Raises on a half-set or invalid configuration, so boot fails rather than a turn.
    """
    global _router, _loaded
    with _lock:
        if not _loaded:
            config = GatewayConfig.from_env(os.environ if env is None else env)
            _router = ModelRouter(config) if config is not None else None
            _loaded = True
        return _router


def use_model_router(router: ModelRouter | None) -> None:
    """Install ``router`` as the process's router (tests, embedding)."""
    global _router, _loaded
    with _lock:
        _router, _loaded = router, True


def reset_model_router() -> None:
    """Forget the router; the next :func:`model_router` call reads the environment again."""
    global _router, _loaded
    with _lock:
        _router, _loaded = None, False
