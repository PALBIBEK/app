"""Temporal client shared by the API (starts and messages session workflows) and the worker."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

from supervisor.config import TemporalSettings
from supervisor.temporal.codec import data_converter


async def connect(settings: TemporalSettings, *, lazy: bool = False) -> Client:
    """Connect with the converter and tracing every caller must agree on.

    The same data converter must be used by every client and worker, or
    payloads written by one cannot be read by the other: with a codec key, every one
    must hold it (and the old keys, during a rotation).
    """
    return await Client.connect(
        settings.address,
        namespace=settings.namespace,
        api_key=settings.api_key,
        tls=settings.api_key is not None,
        data_converter=data_converter(settings.codec_key, settings.codec_old_keys),
        interceptors=[TracingInterceptor()],
        lazy=lazy,
    )
