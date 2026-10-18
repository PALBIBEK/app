"""Generic async BaseService with lifecycle hooks."""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

ModelType = TypeVar("ModelType")


class BaseService(Generic[ModelType]):
    """Classmethod-based CRUD service bound to an ORM model."""

    model: ClassVar[type]

    @classmethod
    async def pre_save_check(cls, data: dict[str, Any], db: AsyncSession) -> None:
        """Validate before create/update. Override in subclasses."""

    @classmethod
    async def pre_save(cls, data: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
        return data

    @classmethod
    async def post_save(cls, instance: ModelType, db: AsyncSession) -> None:
        return None

    @classmethod
    async def pre_delete_check(cls, instance: ModelType, db: AsyncSession) -> None:
        return None

    @classmethod
    async def pre_delete(cls, instance: ModelType, db: AsyncSession) -> None:
        return None

    @classmethod
    async def post_delete(cls, instance: ModelType, db: AsyncSession) -> None:
        return None

    @classmethod
    async def aget_one(
        cls,
        id: str,
        db: AsyncSession,
        *,
        error: bool = True,
    ) -> ModelType | None:
        obj = await db.get(cls.model, id)
        if obj is None and error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{cls.model.__name__} not found",
            )
        return obj

    @classmethod
    async def aget_all(
        cls,
        db: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 100,
        **filters: Any,
    ) -> list[ModelType]:
        stmt = select(cls.model)
        for key, value in filters.items():
            if value is None or not hasattr(cls.model, key):
                continue
            stmt = stmt.where(getattr(cls.model, key) == value)
        stmt = stmt.offset(skip).limit(limit)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def count(cls, db: AsyncSession, **filters: Any) -> int:
        stmt = select(func.count()).select_from(cls.model)
        for key, value in filters.items():
            if value is None or not hasattr(cls.model, key):
                continue
            stmt = stmt.where(getattr(cls.model, key) == value)
        result = await db.execute(stmt)
        return int(result.scalar_one())

    @classmethod
    async def acreate(cls, data: dict[str, Any], db: AsyncSession) -> ModelType:
        await cls.pre_save_check(data, db)
        data = await cls.pre_save(data, db)
        instance = cls.model(**data)
        db.add(instance)
        await db.commit()
        await db.refresh(instance)
        await cls.post_save(instance, db)
        return instance

    @classmethod
    async def aupdate(
        cls,
        id: str,
        data: dict[str, Any],
        db: AsyncSession,
    ) -> ModelType:
        instance = await cls.aget_one(id, db)
        await cls.pre_save_check({**data, "user_id": getattr(instance, "user_id", None)}, db)
        data = await cls.pre_save(data, db)
        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)
        await db.commit()
        await db.refresh(instance)
        await cls.post_save(instance, db)
        return instance

    @classmethod
    async def adelete(cls, id: str, db: AsyncSession) -> None:
        instance = await cls.aget_one(id, db)
        await cls.pre_delete_check(instance, db)
        await cls.pre_delete(instance, db)
        await db.delete(instance)
        await db.commit()
        await cls.post_delete(instance, db)
