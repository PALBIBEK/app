"""Environments: create, change, delete, and resolve the one a manifest names.

A manifest that names an environment takes its sandbox from it whenever the draft is read
or published, so changing an environment shows as an unpublished change on every harness
that uses it; publishing copies the settings into the release (releases never change).
"""

from __future__ import annotations

from uuid import uuid4

from fastapi import HTTPException, status
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.db.models.environment import Environment
from prime_agent.db.models.harness import Harness
from prime_agent.db.models.harness_release import HarnessDraft, HarnessRelease
from prime_agent.schemas.environment import EnvironmentUser, EnvironmentView, EnvironmentWrite
from prime_agent.schemas.harness_manifest import HarnessManifest, SandboxSpec, manifest_body


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Environment not found")


def _spec(env: Environment) -> SandboxSpec:
    return SandboxSpec.model_validate(env.spec)


class EnvironmentService:
    @staticmethod
    async def get_owned(environment_id: str, user_id: str, db: AsyncSession) -> Environment:
        env = await db.get(Environment, environment_id)
        if env is None or env.user_id != user_id:
            raise _not_found()
        return env

    @staticmethod
    async def resolve(manifest: HarnessManifest, user_id: str, db: AsyncSession) -> HarnessManifest:
        """The manifest with its environment's sandbox in place (unchanged without one)."""
        if manifest.environment_id is None:
            return manifest
        env = await db.get(Environment, manifest.environment_id)
        if env is None or env.user_id != user_id:
            # A validation error on the field, so the builder shows it in place.
            raise RequestValidationError([{
                "loc": ("body", "environment_id"),
                "msg": "this environment does not exist (it may have been deleted): pick another",
                "type": "value_error",
            }])
        return manifest.model_copy(update={"sandbox": _spec(env)})

    @staticmethod
    async def _users(env: Environment, db: AsyncSession) -> list[EnvironmentUser]:
        rows = await db.execute(
            select(Harness, HarnessDraft).join(HarnessDraft, HarnessDraft.harness_id == Harness.id)
            .where(Harness.user_id == env.user_id)
        )
        users: list[EnvironmentUser] = []
        for harness, draft in rows.all():
            if (draft.manifest or {}).get("environment_id") != env.id:
                continue
            latest = (
                await db.execute(
                    select(HarnessRelease).where(HarnessRelease.harness_id == harness.id)
                    .order_by(HarnessRelease.version.desc()).limit(1)
                )
            ).scalar_one_or_none()
            resolved = HarnessManifest.model_validate(draft.manifest).model_copy(update={"sandbox": _spec(env)})
            users.append(EnvironmentUser(
                id=harness.id, name=harness.name,
                published=latest is not None and latest.manifest == manifest_body(resolved),
            ))
        return sorted(users, key=lambda u: u.name.lower())

    @classmethod
    async def view(cls, env: Environment, db: AsyncSession) -> EnvironmentView:
        return EnvironmentView(
            id=env.id, name=env.name, description=env.description, sandbox=_spec(env),
            used_by=await cls._users(env, db), created_at=env.created_at, updated_at=env.updated_at,
        )

    @classmethod
    async def list(cls, user_id: str, db: AsyncSession) -> list[EnvironmentView]:
        rows = await db.execute(select(Environment).where(Environment.user_id == user_id).order_by(Environment.name))
        return [await cls.view(env, db) for env in rows.scalars().all()]

    @staticmethod
    async def _check_name(user_id: str, name: str, db: AsyncSession, *, other_than: str | None = None) -> None:
        """Before anything is added or changed (an autoflush would insert the duplicate first)."""
        query = select(Environment.id).where(Environment.user_id == user_id, Environment.name == name)
        if other_than is not None:
            query = query.where(Environment.id != other_than)
        if (await db.execute(query)).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"you already have an environment named {name!r}"
            )

    @staticmethod
    async def _save(env: Environment, db: AsyncSession) -> None:
        try:
            await db.commit()
        except IntegrityError as exc:  # a concurrent save took the name
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"you already have an environment named {env.name!r}"
            ) from exc
        await db.refresh(env)

    @classmethod
    async def create(cls, user_id: str, body: EnvironmentWrite, db: AsyncSession) -> EnvironmentView:
        await cls._check_name(user_id, body.name.strip(), db)
        env = Environment(
            id=str(uuid4()),
            user_id=user_id, name=body.name.strip(), description=body.description.strip(),
            spec=body.sandbox.model_dump(mode="json", exclude_none=True),
        )
        db.add(env)
        await cls._save(env, db)
        return await cls.view(env, db)

    @classmethod
    async def update(cls, env: Environment, body: EnvironmentWrite, db: AsyncSession) -> EnvironmentView:
        await cls._check_name(env.user_id, body.name.strip(), db, other_than=env.id)
        env.name = body.name.strip()
        env.description = body.description.strip()
        env.spec = body.sandbox.model_dump(mode="json", exclude_none=True)
        await cls._save(env, db)
        return await cls.view(env, db)

    @classmethod
    async def delete(cls, env: Environment, db: AsyncSession) -> None:
        if users := await cls._users(env, db):
            names = ", ".join(u.name for u in users[:5])
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{len(users)} harness(es) use this environment ({names}): pick another for them first",
            )
        await db.delete(env)
        await db.commit()
