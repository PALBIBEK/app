"""Temporal client shared by the API (starts and messages session workflows) and the worker."""

from __future__ import annotations

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.contrib.pydantic import pydantic_data_converter

from supervisor.config import TemporalSettings


async def connect(settings: TemporalSettings, *, lazy: bool = False) -> Client:
    """Connect with the converter and tracing every caller must agree on.

    The same data converter must be used by every client and worker, or
    payloads written by one cannot be read by the other.
    """
    return await Client.connect(
        settings.address,
        namespace=settings.namespace,
        api_key=settings.api_key,
        tls=settings.api_key is not None,
        data_converter=pydantic_data_converter,
        interceptors=[TracingInterceptor()],
        lazy=lazy,
    )
