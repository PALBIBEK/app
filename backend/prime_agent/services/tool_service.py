"""
Tool service with business logic and validation.

Following corridor-platforms pattern with:
- BaseService extension
- Lifecycle hooks for validation
- Domain-specific business logic
"""

from __future__ import annotations

import socket
import time
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.tool import Tool
from prime_agent.schemas.tool import ToolExecuteResponse, ToolExecutionResult
from prime_agent.services.base import BaseService

# Lab execute is owner QA outside the agent journal — keep timeouts tight.
LAB_EXECUTE_DEFAULT_TIMEOUT_S = 30.0
LAB_EXECUTE_MAX_TIMEOUT_S = 60.0


class ToolService(BaseService[Tool]):
    """Service for tool management."""

    model = Tool

    VALID_CATEGORIES = [
        "search",
        "data",
        "code",
        "communication",
        "system",
        "ai",
        "custom",
    ]
    VALID_STATUSES = ["active", "inactive", "deprecated", "beta"]

    @classmethod
    async def pre_save_check(cls, data: dict[str, Any], db: AsyncSession) -> None:
        user_id = data.get("user_id")
        name = data.get("name")
        if name and user_id:
            stmt = select(Tool).where(Tool.user_id == user_id, Tool.name == name)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            exclude_id = data.get("_exclude_id")
            if existing is not None and existing.id != exclude_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Tool with name '{name}' already exists",
                )

        category = data.get("category")
        if category is not None and category not in cls.VALID_CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category. Must be one of: {', '.join(cls.VALID_CATEGORIES)}",
            )

        status_value = data.get("status")
        if status_value is not None and status_value not in cls.VALID_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status. Must be one of: {', '.join(cls.VALID_STATUSES)}",
            )

        rate_limit = data.get("rate_limit")
        if rate_limit is not None and rate_limit <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="rate_limit must be a positive integer",
            )

        endpoint = data.get("endpoint")
        if endpoint:
            cls._validate_endpoint(endpoint)

    @classmethod
    async def get_by_name(
        cls, name: str, db: AsyncSession, *, error: bool = False
    ) -> Tool | None:
        stmt = select(Tool).where(Tool.name == name)
        tool = (await db.execute(stmt)).scalar_one_or_none()
        if tool is None and error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
        return tool

    @classmethod
    async def get_by_category(cls, category: str, db: AsyncSession) -> list[Tool]:
        stmt = select(Tool).where(Tool.category == category)
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def get_by_status(cls, status_value: str, db: AsyncSession) -> list[Tool]:
        stmt = select(Tool).where(Tool.status == status_value)
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def get_active_tools(cls, db: AsyncSession) -> list[Tool]:
        return await cls.get_by_status("active", db)

    @classmethod
    async def get_owned(cls, tool_id: str, user_id: str, db: AsyncSession) -> Tool:
        tool = await cls.aget_one(tool_id, db)
        if tool.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
        return tool

    @classmethod
    async def aupdate(
        cls,
        id: str,
        data: dict[str, Any],
        db: AsyncSession,
    ) -> Tool:
        instance = await cls.aget_one(id, db)
        check_data = {**data, "user_id": instance.user_id, "_exclude_id": instance.id}
        await cls.pre_save_check(check_data, db)
        data = await cls.pre_save(data, db)
        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await db.commit()
        await db.refresh(instance)
        await cls.post_save(instance, db)
        return instance

    @staticmethod
    def _validate_endpoint(endpoint: str, *, resolve_dns: bool = False) -> None:
        """Reject SSRF targets. With ``resolve_dns``, re-check resolved A/AAAA (DNS rebinding)."""
        try:
            parsed = urlsplit(endpoint)
            _ = parsed.port
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Endpoint must be a public HTTP(S) URL",
            ) from None

        hostname = (parsed.hostname or "").lower().rstrip(".")
        hostname_parts = hostname.split(".")
        numeric_hostname = (
            hostname.isdigit()
            or all(part.isdigit() for part in hostname_parts)
            or (
                hostname.startswith("0x")
                and all(character in "0123456789abcdef" for character in hostname[2:])
            )
        )
        invalid_hostname = (
            not hostname
            or numeric_hostname
            or hostname == "localhost"
            or hostname.endswith((".localhost", ".local", ".internal"))
            or parsed.username is not None
            or parsed.password is not None
        )
        if parsed.scheme not in {"http", "https"} or invalid_hostname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Endpoint must be a public HTTP(S) URL",
            )

        try:
            address = ip_address(hostname)
        except ValueError:
            address = None

        if address is not None and not address.is_global:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Endpoint must be a public HTTP(S) URL",
            )

        if not resolve_dns or address is not None:
            return

        try:
            infos = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Endpoint hostname could not be resolved",
            ) from exc

        if not infos:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Endpoint hostname could not be resolved",
            )

        for info in infos:
            resolved = info[4][0]
            try:
                resolved_addr = ip_address(resolved)
            except ValueError:
                continue
            if not resolved_addr.is_global:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Endpoint must be a public HTTP(S) URL",
                )

    @classmethod
    async def execute_lab(
        cls,
        tool_id: str,
        user_id: str,
        parameters: dict[str, Any],
        db: AsyncSession,
        *,
        timeout_s: float | None = None,
    ) -> ToolExecuteResponse:
        """Owner-scoped Lab execute — SSRF re-check at call time; not a journal substitute."""
        tool = await cls.get_owned(tool_id, user_id, db)
        execution_id = str(uuid4())
        started = time.perf_counter()
        now = datetime.now(UTC)

        if tool.status != "active":
            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="error",
                    error=f"Tool status is {tool.status!r}; only active tools can execute",
                    duration_ms=0.0,
                    timestamp=now,
                ),
            )

        if not tool.endpoint:
            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="error",
                    error="Tool has no endpoint; Lab execute requires an HTTP(S) endpoint",
                    duration_ms=0.0,
                    timestamp=now,
                ),
            )

        if tool.auth_required:
            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="error",
                    error="Lab execute does not supply auth credentials for auth_required tools",
                    duration_ms=0.0,
                    timestamp=now,
                ),
            )

        # Re-validate at call time (DNS rebinding) — do not trust create-time check alone.
        cls._validate_endpoint(tool.endpoint, resolve_dns=True)

        timeout = LAB_EXECUTE_DEFAULT_TIMEOUT_S
        if timeout_s is not None:
            timeout = max(1.0, min(float(timeout_s), LAB_EXECUTE_MAX_TIMEOUT_S))

        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                response = await client.post(
                    tool.endpoint,
                    json={"parameters": parameters},
                )
            duration_ms = (time.perf_counter() - started) * 1000.0
            try:
                body: Any = response.json()
            except ValueError:
                body = {"text": response.text[:4096]}

            if response.status_code >= 400:
                return ToolExecuteResponse(
                    execution_id=execution_id,
                    result=ToolExecutionResult(
                        tool_id=tool.id,
                        execution_id=execution_id,
                        status="error",
                        error=f"Upstream HTTP {response.status_code}",
                        result=body,
                        duration_ms=duration_ms,
                        timestamp=datetime.now(UTC),
                    ),
                )

            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="success",
                    result=body,
                    duration_ms=duration_ms,
                    timestamp=datetime.now(UTC),
                ),
            )
        except httpx.TimeoutException:
            duration_ms = (time.perf_counter() - started) * 1000.0
            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="timeout",
                    error=f"Lab execute timed out after {timeout}s",
                    duration_ms=duration_ms,
                    timestamp=datetime.now(UTC),
                ),
            )
        except httpx.HTTPError as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            return ToolExecuteResponse(
                execution_id=execution_id,
                result=ToolExecutionResult(
                    tool_id=tool.id,
                    execution_id=execution_id,
                    status="error",
                    error=f"Lab execute request failed: {exc.__class__.__name__}",
                    duration_ms=duration_ms,
                    timestamp=datetime.now(UTC),
                ),
            )
