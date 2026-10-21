"""Harness service with validation and ownership helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.harness import Harness
from prime_agent.db.models.tool import Tool
from prime_agent.schemas.harness import normalize_harness_mode
from prime_agent.services.base import BaseService


class HarnessService(BaseService[Harness]):
    """Service for harness management."""

    model = Harness

    # Canonical Run modes (AI-aligned). Legacy chat|completion|agent accepted via normalize.
    VALID_MODES = [
        "sticky",
        "stateless",
    ]

    @classmethod
    async def pre_save_check(cls, data: dict[str, Any], db: AsyncSession) -> None:
        user_id = data.get("user_id")
        name = data.get("name")
        if name and user_id:
            stmt = select(Harness).where(Harness.user_id == user_id, Harness.name == name)
            existing = (await db.execute(stmt)).scalar_one_or_none()
            exclude_id = data.get("_exclude_id")
            if existing is not None and existing.id != exclude_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Harness with name '{name}' already exists",
                )

        # ``model`` is a label: the model a session runs is its manifest's, which the
        # model gateway checks when the harness is published.
        # Validate + normalize mode (sticky|stateless; legacy chat→sticky, completion|agent→stateless)
        if "mode" in data and data["mode"] is not None:
            try:
                data["mode"] = normalize_harness_mode(str(data["mode"]))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

        if "temperature" in data and data["temperature"] is not None:
            temp = data["temperature"]
            if temp < 0 or temp > 2:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="temperature must be between 0 and 2",
                )

        if "max_tokens" in data and data["max_tokens"] is not None:
            if data["max_tokens"] <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="max_tokens must be a positive integer",
                )

        tool_ids = data.get("tools")
        if tool_ids:
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="user_id required to attach tools",
                )
            for tool_id in tool_ids:
                tool = await db.get(Tool, tool_id)
                if tool is None or tool.user_id != user_id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Tool not found",
                    )

        if data.get("memory"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Memory stores are not available yet — harness.memory[] attach "
                    "is not the product Memory durable bank (use /memory BFF scopes)"
                ),
            )

    @classmethod
    async def aupdate(
        cls,
        id: str,
        data: dict[str, Any],
        db: AsyncSession,
    ) -> Harness:
        instance = await cls.aget_one(id, db)
        check_data = {**data, "user_id": instance.user_id, "_exclude_id": instance.id}
        await cls.pre_save_check(check_data, db)
        # mode may have been normalized on check_data
        if "mode" in check_data:
            data = {**data, "mode": check_data["mode"]}
        data = await cls.pre_save(data, db)
        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await db.commit()
        await db.refresh(instance)
        await cls.post_save(instance, db)
        return instance

    @classmethod
    async def get_by_name(
        cls, name: str, db: AsyncSession, *, error: bool = False
    ) -> Harness | None:
        stmt = select(Harness).where(Harness.name == name)
        harness = (await db.execute(stmt)).scalar_one_or_none()
        if harness is None and error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Harness not found"
            )
        return harness

    @classmethod
    async def get_by_model(cls, model: str, db: AsyncSession) -> list[Harness]:
        stmt = select(Harness).where(Harness.model == model)
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def get_by_mode(cls, mode: str, db: AsyncSession) -> list[Harness]:
        try:
            normalized = normalize_harness_mode(mode)
        except ValueError:
            normalized = mode
        stmt = select(Harness).where(Harness.mode == normalized)
        return list((await db.execute(stmt)).scalars().all())

    @classmethod
    async def get_owned(cls, harness_id: str, user_id: str, db: AsyncSession) -> Harness:
        harness = await cls.aget_one(harness_id, db)
        if harness.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Harness not found"
            )
        return harness
