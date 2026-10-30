"""Temporal client shared by the API (starts and messages session workflows) and the worker."""

from __future__ import annotations

import os
from typing import Final

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

from supervisor.config import TemporalSettings
from supervisor.temporal.codec import data_converter

__all__ = ["CodecKeyMissing", "connect", "require_codec_key"]

_STRICT_ENVS: Final = frozenset({"production", "prod", "staging"})


class CodecKeyMissing(RuntimeError):
    """Production would hand Temporal its histories in plain text."""


def require_codec_key(settings: TemporalSettings, env: dict[str, str] | None = None) -> None:
    """In production and staging, refuse to run without a history key: what a session's
    workflow carries (messages, answers, tool results, errors) would be stored by the
    Temporal server as plain text. ``TEMPORAL_CODEC_OLD_KEYS`` alone is the first step of
    turning encryption on (it reads sealed payloads, writes plain ones) and is allowed;
    ``TEMPORAL_PLAINTEXT_HISTORY=1`` says plain history is meant (the server encrypts at rest).
    """
    env = dict(os.environ) if env is None else env
    deployment = (env.get("ENV") or env.get("ENVIRONMENT") or "").strip().lower()
    if deployment not in _STRICT_ENVS or settings.codec_key or settings.codec_old_keys:
        return
    if env.get("TEMPORAL_PLAINTEXT_HISTORY", "").strip().lower() in ("1", "true", "yes"):
        return
    raise CodecKeyMissing(
        "TEMPORAL_CODEC_KEY must be set in production and staging: without it Temporal stores "
        "every session's messages and tool results as plain text (supervisor.temporal.codec). "
        "Set TEMPORAL_PLAINTEXT_HISTORY=1 only if the Temporal server encrypts at rest."
    )


async def connect(settings: TemporalSettings, *, lazy: bool = False) -> Client:
    """Connect with the converter and tracing every caller must agree on.

    The same data converter must be used by every client and worker, or
    payloads written by one cannot be read by the other: with a codec key, every one
    must hold it (and the old keys, during a rotation). Production needs a key
    (``require_codec_key``).
    """
    require_codec_key(settings)
    return await Client.connect(
        settings.address,
        namespace=settings.namespace,
        api_key=settings.api_key,
        tls=settings.api_key is not None,
        data_converter=data_converter(settings.codec_key, settings.codec_old_keys),
        interceptors=[TracingInterceptor()],
        lazy=lazy,
    )
