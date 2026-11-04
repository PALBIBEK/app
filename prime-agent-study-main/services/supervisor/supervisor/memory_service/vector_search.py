"""Nearest-neighbour candidates for bank recall, computed in Postgres with pgvector.

Bank recall read every live item of a scope, embeddings included, and ranked them in
Python: its cost grew with the bank. The systems this follows search in the database
and rank a bounded pool:

- mem0 (mem0ai/mem0, mem0/vector_stores/pgvector.py): ``CREATE EXTENSION IF NOT EXISTS
  vector``, a ``vector(dims)`` column, ``ORDER BY vector <=> %s::vector LIMIT %s``; and
  (mem0/memory/main.py ``_search_vector_store``) a pool of ``max(limit * 4, 60)``
  candidates for its default ``top_k`` of 20, scored afterwards.
- pgvector (pgvector/pgvector README, "Filtering"): for a ``WHERE`` that matches a low
  percentage of rows (one scope's items), an index on the filter columns gives fast,
  *exact* nearest-neighbour search; an approximate (HNSW) index filters after the scan
  and, at the default ``hnsw.ef_search`` of 40, returns fewer rows than asked.

So on Postgres with the extension, ``memory_item`` gets ``embedding_vec vector(1024)``,
kept in step with the JSON ``embedding`` by a trigger (writers stay database-neutral),
and recall asks for the CANDIDATES nearest live items of the scope, through the
partial scope index. Without the extension (or on SQLite, the local store) recall scans.
"""

from __future__ import annotations

import json
import logging
from typing import Final

from sqlalchemy import Connection, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from supervisor.memory_service.embed import EMBED_DIM
from supervisor.memory_service.models import MemoryItemRow
from supervisor.memory_service.types import Scope

logger = logging.getLogger(__name__)

CANDIDATES: Final = 80
"""mem0's pool for its default top_k of 20: max(20 * 4, 60)."""

_COLUMN: Final = "embedding_vec"


def ensure(conn: Connection) -> bool:
    """On Postgres: the vector column, its trigger and backfill, if pgvector is installable.
    Idempotent; run at every startup (so installing the extension later turns it on)."""
    if conn.dialect.name != "postgresql":
        return False
    if conn.execute(text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).first() is None:
        logger.warning("pgvector is not available: bank recall scans every item of a scope")
        return False
    try:
        with conn.begin_nested():
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    except DBAPIError:
        logger.warning("pgvector could not be enabled (CREATE EXTENSION): bank recall scans", exc_info=True)
        return False
    has_column = conn.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = 'memory_item' AND column_name = :c"),
        {"c": _COLUMN},
    ).first()
    if has_column is None:
        conn.execute(text(f"ALTER TABLE memory_item ADD COLUMN {_COLUMN} vector({EMBED_DIM})"))
    conn.execute(
        text(
            f"""
            CREATE OR REPLACE FUNCTION memory_item_embedding_vec() RETURNS trigger AS $$
            BEGIN
                NEW.{_COLUMN} := CASE WHEN json_array_length(NEW.embedding) = {EMBED_DIM}
                                      THEN NEW.embedding::text::vector ELSE NULL END;
                RETURN NEW;
            END $$ LANGUAGE plpgsql
            """
        )
    )
    conn.execute(text("DROP TRIGGER IF EXISTS memory_item_embedding_vec ON memory_item"))
    conn.execute(
        text(
            "CREATE TRIGGER memory_item_embedding_vec BEFORE INSERT OR UPDATE OF embedding ON memory_item "
            "FOR EACH ROW EXECUTE FUNCTION memory_item_embedding_vec()"
        )
    )
    conn.execute(
        text(
            f"UPDATE memory_item SET {_COLUMN} = embedding::text::vector "
            f"WHERE {_COLUMN} IS NULL AND json_array_length(embedding) = {EMBED_DIM}"
        )
    )
    return True


async def available(db: AsyncSession) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    found = await db.scalar(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = 'memory_item' AND column_name = :c"),
        {"c": _COLUMN},
    )
    return found is not None


async def nearest(db: AsyncSession, scope: Scope, query_vector: list[float], *, limit: int = CANDIDATES) -> list[MemoryItemRow]:
    """The ``limit`` live items of ``scope`` nearest ``query_vector`` (cosine), exactly."""
    query = (
        select(MemoryItemRow)
        .where(
            MemoryItemRow.tenant == scope.tenant,
            MemoryItemRow.subject == scope.subject,
            MemoryItemRow.namespace == scope.namespace,
            MemoryItemRow.superseded_by.is_(None),
            MemoryItemRow.forgotten == 0,
            text(f"{_COLUMN} IS NOT NULL"),
        )
        .order_by(text(f"{_COLUMN} <=> CAST(:query_vector AS vector)"))
        .limit(limit)
    )
    result = await db.execute(query, {"query_vector": json.dumps(query_vector)})
    return list(result.scalars().all())
