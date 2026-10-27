"""What happens to a run's trace when the run ends, beside the event path.

The rollup and its checks are written; then, for an agent that has judges, each judge gives
its verdict; and where ``TRACE_SUMMARY_MODEL`` is set, a model writes the run's summary.
Model calls go through the session's own gateway client. Nothing here can fail a run: every
step logs and moves on.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supervisor.db.models import SessionRow
from supervisor.journal.reader import JournalReader
from supervisor.seams.interfaces.model_gateway import ModelGatewayClient
from supervisor.traces.judges import judge_run
from supervisor.traces.model import RunTrace
from supervisor.traces.store import load_run, set_model_summary
from supervisor.traces.summary import SUMMARY_MODEL_ENV, clean_summary, summary_messages

__all__ = ["ClientFor", "after_run"]

logger = logging.getLogger(__name__)

ClientFor = Callable[[SessionRow], ModelGatewayClient | None]
"""The model client a session's own calls use (its key and budget), if it has one now."""


async def _summarise(
    factory: async_sessionmaker[AsyncSession], trace: RunTrace, client: ModelGatewayClient, model: str
) -> None:
    def ask() -> str:
        return str(client.complete(summary_messages(trace), [], model=model, tool_choice="none").message.content or "")

    text = clean_summary(await asyncio.to_thread(ask))
    if text:
        await set_model_summary(factory, trace.session_id, trace.run_id, text)


async def after_run(
    factory: async_sessionmaker[AsyncSession],
    reader: JournalReader,
    session_id: str,
    run_id: str,
    client_for: ClientFor,
) -> RunTrace | None:
    """Record the run's trace, then judge and summarise it if it finished."""
    try:
        async with factory() as db:
            session = await db.get(SessionRow, session_id)
        if session is None:
            return None
        trace = await load_run(factory, reader, session, run_id)
    except Exception:
        logger.exception("trace rollup failed session=%s run=%s", session_id, run_id)
        return None
    if trace is None or not trace.rollup.status.finished:
        return trace
    client = None
    try:
        client = client_for(session)
    except Exception:
        logger.warning("no model client for session %s; judges and summaries skipped", session_id, exc_info=True)
    try:
        await judge_run(factory, session, trace, client)
    except Exception:
        logger.exception("judging run %s failed", run_id)
    model = os.environ.get(SUMMARY_MODEL_ENV, "").strip()
    if model and client is not None:
        try:
            await _summarise(factory, trace, client, model)
        except Exception:
            logger.warning("summarising run %s failed", run_id, exc_info=True)
    return trace
