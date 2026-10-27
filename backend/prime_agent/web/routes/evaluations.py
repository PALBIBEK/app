"""Datasets of saved runs and experiments that replay them on a version (under ``/traces``).

Every route answers only for the user's own agents, datasets and experiments; anything else
is a 404. Starting an experiment answers at once; it replays in the background and its page
shows progress.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from prime_agent.config import get_settings
from prime_agent.db.session import db as database
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.schemas.evaluation import (
    AddRunRequest,
    DatasetCreate,
    DatasetDetail,
    DatasetItemOut,
    DatasetOut,
    ExperimentComparison,
    ExperimentDetail,
    ExperimentOut,
    ExperimentStart,
)
from prime_agent.services.evaluation_service import EvaluationNotFoundError, EvaluationService
from prime_agent.web.dependencies import CurrentUser, DbSession

router = APIRouter()

_running: set[asyncio.Task[Any]] = set()
"""Experiments replaying in this process (held so none is collected mid-run)."""


def _service() -> EvaluationService:
    return EvaluationService.from_settings(get_settings())


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


def launch(service: EvaluationService, experiment_id: str, identity: TenantPrincipal) -> None:
    """Replay the experiment in the background, on its own database sessions."""
    factory = database._session_factory  # noqa: SLF001 - the app's one session factory
    if factory is None:
        raise RuntimeError("the database is not initialised")
    task = asyncio.create_task(service.run_experiment(experiment_id, identity=identity, session_factory=factory))
    _running.add(task)
    task.add_done_callback(_running.discard)


@router.get("/agents/{agent_id}/datasets", response_model=list[DatasetOut])
async def list_datasets(agent_id: str, db: DbSession, current_user: CurrentUser) -> list[DatasetOut]:
    try:
        return await _service().list_datasets(user_id=current_user.id, agent_id=agent_id, db=db)
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.post("/agents/{agent_id}/datasets", response_model=DatasetOut, status_code=201)
async def create_dataset(agent_id: str, body: DatasetCreate, db: DbSession, current_user: CurrentUser) -> DatasetOut:
    try:
        return await _service().create_dataset(
            user_id=current_user.id, agent_id=agent_id, name=body.name, description=body.description, db=db
        )
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail)
async def dataset_detail(dataset_id: str, db: DbSession, current_user: CurrentUser) -> DatasetDetail:
    """The dataset's saved runs and the experiments run on it, with how each version did."""
    try:
        return await _service().detail(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, dataset_id=dataset_id, db=db
        )
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.delete("/datasets/{dataset_id}", status_code=204, response_class=Response)
async def delete_dataset(dataset_id: str, db: DbSession, current_user: CurrentUser) -> Response:
    try:
        await _service().delete_dataset(user_id=current_user.id, dataset_id=dataset_id, db=db)
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=204)


@router.post("/datasets/{dataset_id}/items", response_model=DatasetItemOut, status_code=201)
async def add_run(dataset_id: str, body: AddRunRequest, db: DbSession, current_user: CurrentUser) -> DatasetItemOut:
    """Save one of the user's runs to the dataset, to replay later."""
    try:
        return await _service().add_run(
            identity=TenantPrincipal.from_user(current_user),
            user_id=current_user.id,
            dataset_id=dataset_id,
            session_id=body.session_id,
            run_id=body.run_id,
            expected=body.expected,
            db=db,
        )
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.delete("/datasets/{dataset_id}/items/{item_id}", status_code=204, response_class=Response)
async def delete_item(dataset_id: str, item_id: str, db: DbSession, current_user: CurrentUser) -> Response:
    try:
        await _service().delete_item(user_id=current_user.id, dataset_id=dataset_id, item_id=item_id, db=db)
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=204)


@router.post("/datasets/{dataset_id}/experiments", response_model=ExperimentOut, status_code=202)
async def start_experiment(
    dataset_id: str, body: ExperimentStart, db: DbSession, current_user: CurrentUser
) -> ExperimentOut:
    """Replay every saved run on a version of the agent (the live one by default)."""
    service = _service()
    try:
        dataset = await service._dataset(db, current_user.id, dataset_id)  # noqa: SLF001
        agent = await service._agent(db, current_user.id, dataset.harness_id)  # noqa: SLF001
        pin = await service.pin_for(db, agent, body.version)
        exp = await service.create_experiment(user_id=current_user.id, dataset_id=dataset_id, pin=pin, db=db)
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc
    launch(service, exp.id, TenantPrincipal.from_user(current_user))
    return service._exp_out(exp, [])  # noqa: SLF001


@router.get("/experiments/compare", response_model=ExperimentComparison)
async def compare(a: str, b: str, db: DbSession, current_user: CurrentUser) -> ExperimentComparison:
    """Two experiments of one dataset side by side, item by item."""
    try:
        return await _service().compare(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, a_id=a, b_id=b, db=db
        )
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.get("/experiments/{experiment_id}", response_model=ExperimentDetail)
async def experiment(experiment_id: str, db: DbSession, current_user: CurrentUser) -> ExperimentDetail:
    try:
        return await _service().experiment(
            identity=TenantPrincipal.from_user(current_user), user_id=current_user.id, experiment_id=experiment_id, db=db
        )
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc


@router.post("/experiments/{experiment_id}/cancel", status_code=204, response_class=Response)
async def cancel(experiment_id: str, db: DbSession, current_user: CurrentUser) -> Response:
    try:
        await _service().cancel(user_id=current_user.id, experiment_id=experiment_id, db=db)
    except EvaluationNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=204)
