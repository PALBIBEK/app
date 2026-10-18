"""
Session store with PostgreSQL persistence.

Provides durable storage for sessions, message history, and lease-based concurrency control.
Following QM architecture patterns (durable by default).

Classification: persistence layer (database-backed)
"""

from __future__ import annotations

import typing as t
import uuid
from dataclasses import dataclass
from decimal import Decimal
import time

import asyncpg


@dataclass(frozen=True)
class Session:
    """Session metadata"""
    id: str
    scope_id: str
    model: str
    created_at: int
    updated_at: int
    last_activity: int
    metadata: dict[str, t.Any]


@dataclass(frozen=True)
class SessionEntry:
    """Message history entry"""
    session_id: str
    seq: int
    type: str  # "user_message", "assistant_message", "tool_call", "tool_result"
    created_at: int
    payload: dict[str, t.Any]


@dataclass(frozen=True)
class Lease:
    """Lease for exclusive session access"""
    session_id: str
    token: str


@dataclass(frozen=True)
class LeaseAttempt:
    """Result of lease acquisition attempt"""
    lease: Lease | None
    held_by: str | None = None
    held_since: int | None = None
    held_until: int | None = None


@dataclass(frozen=True)
class TapeRecord:
    """Event journal record"""
    session_id: str
    seq: int
    kind: str  # "message", "context_event", "annotation"
    payload: dict[str, t.Any]
    created_at: int
    entry_seq: int | None = None


@dataclass(frozen=True)
class LlmRequestRecord:
    """LLM request tracking record"""
    id: str
    session_id: str
    turn_seq: int | None
    step: int
    model: str
    created_at: int
    request: dict[str, t.Any] | None
    response: dict[str, t.Any] | None
    ttft_ms: int | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: Decimal | None


class PostgresSessionStore:
    """
    PostgreSQL-backed session store.

    Implements durable storage for:
    - Sessions (conversation metadata)
    - Entries (message history)
    - Leases (concurrency control)
    - Tape (event journal)
    - LLM requests (cost tracking)

    Pattern: Following QM's "durable by default" principle
    - Everything important persisted to PostgreSQL
    - Never rely on in-memory state
    - Lease-based concurrency prevents race conditions
    """

    def __init__(self, pool: asyncpg.Pool, *, now: t.Callable[[], int] | None = None):
        """
        Initialize session store.

        Args:
            pool: asyncpg connection pool
            now: Optional timestamp function (for testing)
        """
        self.pool = pool
        self._now = now or (lambda: int(time.time() * 1000))

    # Session CRUD

    async def create_session(
        self,
        *,
        session_id: str | None = None,
        scope_id: str,
        model: str,
        metadata: dict[str, t.Any] | None = None
    ) -> Session:
        """
        Create new session.

        Args:
            session_id: Optional explicit session ID (default: generate UUID)
            scope_id: Scope this session belongs to
            model: Model to use
            metadata: Optional metadata dict

        Returns:
            Created session

        Example:
            session = await store.create_session(
                scope_id="user:alice",
                model="claude-sonnet-4-5",
                metadata={"source": "web"}
            )
        """
        session_id = session_id or str(uuid.uuid4())
        now = self._now()

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO githi_sessions (
                    id, scope_id, model, created_at, updated_at, last_activity, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                session_id,
                scope_id,
                model,
                now,
                now,
                now,
                metadata or {}
            )

        return Session(
            id=session_id,
            scope_id=scope_id,
            model=model,
            created_at=now,
            updated_at=now,
            last_activity=now,
            metadata=metadata or {}
        )

    async def get_session(self, session_id: str) -> Session | None:
        """
        Get session by ID.

        Args:
            session_id: Session ID

        Returns:
            Session or None if not found
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, scope_id, model, created_at, updated_at, last_activity, metadata
                FROM githi_sessions
                WHERE id = $1
                """,
                session_id
            )

        if not row:
            return None

        return Session(
            id=row["id"],
            scope_id=row["scope_id"],
            model=row["model"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_activity=row["last_activity"],
            metadata=row["metadata"]
        )

    async def update_session(
        self,
        session_id: str,
        *,
        last_activity: int | None = None,
        metadata: dict[str, t.Any] | None = None
    ) -> None:
        """
        Update session metadata.

        Args:
            session_id: Session ID
            last_activity: Optional last activity timestamp
            metadata: Optional metadata to merge
        """
        now = self._now()

        async with self.pool.acquire() as conn:
            if metadata is not None:
                # Merge metadata
                await conn.execute(
                    """
                    UPDATE githi_sessions
                    SET updated_at = $1,
                        last_activity = $2,
                        metadata = metadata || $3::jsonb
                    WHERE id = $4
                    """,
                    now,
                    last_activity or now,
                    metadata,
                    session_id
                )
            else:
                # Just update timestamps
                await conn.execute(
                    """
                    UPDATE githi_sessions
                    SET updated_at = $1,
                        last_activity = $2
                    WHERE id = $3
                    """,
                    now,
                    last_activity or now,
                    session_id
                )

    # Message history (entries)

    async def append_entries(
        self,
        session_id: str,
        entries: list[dict[str, t.Any]]
    ) -> list[SessionEntry]:
        """
        Append message entries to session.

        Args:
            session_id: Session ID
            entries: List of entry dicts with keys: type, payload

        Returns:
            List of created entries with assigned seq numbers

        Example:
            entries = await store.append_entries(session_id, [
                {"type": "user_message", "payload": {"content": "Hello"}},
                {"type": "assistant_message", "payload": {"content": "Hi there!"}}
            ])
        """
        if not entries:
            return []

        now = self._now()
        created_entries = []

        async with self.pool.acquire() as conn:
            # Use transaction to ensure atomicity
            async with conn.transaction():
                for entry_data in entries:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO githi_session_entries (
                            session_id, type, created_at, payload
                        ) VALUES ($1, $2, $3, $4)
                        RETURNING session_id, seq, type, created_at, payload
                        """,
                        session_id,
                        entry_data["type"],
                        now,
                        entry_data["payload"]
                    )

                    created_entries.append(SessionEntry(
                        session_id=row["session_id"],
                        seq=row["seq"],
                        type=row["type"],
                        created_at=row["created_at"],
                        payload=row["payload"]
                    ))

                # Update session last_activity
                await conn.execute(
                    """
                    UPDATE githi_sessions
                    SET updated_at = $1, last_activity = $2
                    WHERE id = $3
                    """,
                    now,
                    now,
                    session_id
                )

        return created_entries

    async def get_entries(
        self,
        session_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None
    ) -> list[SessionEntry]:
        """
        Get message entries for session.

        Args:
            session_id: Session ID
            since_seq: Only get entries after this seq (default: 0 = all)
            limit: Optional max entries to return

        Returns:
            List of entries ordered by seq

        Example:
            # Get all entries
            entries = await store.get_entries(session_id)

            # Get entries after seq 100
            recent = await store.get_entries(session_id, since_seq=100)
        """
        query = """
            SELECT session_id, seq, type, created_at, payload
            FROM githi_session_entries
            WHERE session_id = $1 AND seq > $2
            ORDER BY seq ASC
        """
        params = [session_id, since_seq]

        if limit is not None:
            query += " LIMIT $3"
            params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            SessionEntry(
                session_id=row["session_id"],
                seq=row["seq"],
                type=row["type"],
                created_at=row["created_at"],
                payload=row["payload"]
            )
            for row in rows
        ]

    # Lease-based concurrency control

    async def acquire_lease(
        self,
        session_id: str,
        holder: str,
        ttl_ms: int
    ) -> LeaseAttempt:
        """
        Acquire exclusive lease on session.

        Pattern: Lease-based concurrency prevents multiple processes
        from handling same session simultaneously. TTL ensures leases
        auto-expire if holder crashes.

        Args:
            session_id: Session ID
            holder: Who's acquiring ("turn", "compaction", "fork")
            ttl_ms: Lease TTL in milliseconds

        Returns:
            LeaseAttempt with lease if acquired, or info about current holder

        Example:
            attempt = await store.acquire_lease(session_id, "turn", 60_000)
            if not attempt.lease:
                print(f"Locked by {attempt.held_by} until {attempt.held_until}")
                return
            try:
                # We have exclusive access
                ...
            finally:
                await store.release_lease(attempt.lease)
        """
        now = self._now()
        expires_at = now + ttl_ms
        token = str(uuid.uuid4())

        async with self.pool.acquire() as conn:
            # Try to insert lease (will fail if already held)
            try:
                await conn.execute(
                    """
                    INSERT INTO githi_session_leases (
                        session_id, token, holder, acquired_at, expires_at
                    ) VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (session_id) DO NOTHING
                    """,
                    session_id,
                    token,
                    holder,
                    now,
                    expires_at
                )
            except Exception:
                # Insert failed, lease held by someone else
                pass

            # Check if we got the lease
            row = await conn.fetchrow(
                """
                SELECT token, holder, acquired_at, expires_at
                FROM githi_session_leases
                WHERE session_id = $1
                """,
                session_id
            )

            if not row:
                # No lease exists (shouldn't happen, but handle gracefully)
                return LeaseAttempt(lease=None)

            if row["token"] == token:
                # We got the lease!
                return LeaseAttempt(
                    lease=Lease(session_id=session_id, token=token)
                )
            else:
                # Someone else has it
                return LeaseAttempt(
                    lease=None,
                    held_by=row["holder"],
                    held_since=row["acquired_at"],
                    held_until=row["expires_at"]
                )

    async def release_lease(self, lease: Lease) -> None:
        """
        Release lease on session.

        Args:
            lease: Lease to release

        Example:
            await store.release_lease(lease)
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM githi_session_leases
                WHERE session_id = $1 AND token = $2
                """,
                lease.session_id,
                lease.token
            )

    # Event journal (tape)

    async def append_tape(
        self,
        session_id: str,
        records: list[dict[str, t.Any]]
    ) -> list[TapeRecord]:
        """
        Append events to session tape.

        Pattern: Tape is event journal for debugging, replay, crash detection.
        Record ALL events, not just state changes.

        Args:
            session_id: Session ID
            records: List of event records with keys: kind, payload, entry_seq?

        Returns:
            List of created tape records with assigned seq numbers

        Example:
            records = await store.append_tape(session_id, [
                {"kind": "message", "payload": {...}, "entry_seq": 123},
                {"kind": "context_event", "payload": {...}}
            ])
        """
        if not records:
            return []

        now = self._now()
        created_records = []

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for record_data in records:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO githi_session_tape (
                            session_id, kind, payload, created_at, entry_seq
                        ) VALUES ($1, $2, $3, $4, $5)
                        RETURNING session_id, seq, kind, payload, created_at, entry_seq
                        """,
                        session_id,
                        record_data["kind"],
                        record_data["payload"],
                        now,
                        record_data.get("entry_seq")
                    )

                    created_records.append(TapeRecord(
                        session_id=row["session_id"],
                        seq=row["seq"],
                        kind=row["kind"],
                        payload=row["payload"],
                        created_at=row["created_at"],
                        entry_seq=row["entry_seq"]
                    ))

        return created_records

    async def get_tape(
        self,
        session_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None
    ) -> list[TapeRecord]:
        """
        Get tape records for session.

        Args:
            session_id: Session ID
            since_seq: Only get records after this seq
            limit: Optional max records to return

        Returns:
            List of tape records ordered by seq
        """
        query = """
            SELECT session_id, seq, kind, payload, created_at, entry_seq
            FROM githi_session_tape
            WHERE session_id = $1 AND seq > $2
            ORDER BY seq ASC
        """
        params = [session_id, since_seq]

        if limit is not None:
            query += " LIMIT $3"
            params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [
            TapeRecord(
                session_id=row["session_id"],
                seq=row["seq"],
                kind=row["kind"],
                payload=row["payload"],
                created_at=row["created_at"],
                entry_seq=row["entry_seq"]
            )
            for row in rows
        ]

    # LLM request tracking

    async def record_llm_request(
        self,
        *,
        session_id: str,
        turn_seq: int | None,
        step: int,
        model: str,
        request: dict[str, t.Any] | None = None,
        response: dict[str, t.Any] | None = None,
        ttft_ms: int | None = None,
        duration_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cost_usd: Decimal | None = None
    ) -> str:
        """
        Record LLM request for tracking.

        Args:
            session_id: Session ID
            turn_seq: Turn sequence number
            step: Step within turn
            model: Model used
            request: Request payload
            response: Response payload
            ttft_ms: Time to first token
            duration_ms: Total duration
            input_tokens: Input token count
            output_tokens: Output token count
            cache_read_tokens: Cache read token count
            cache_write_tokens: Cache write token count
            cost_usd: Cost in USD

        Returns:
            Request ID
        """
        request_id = str(uuid.uuid4())
        now = self._now()

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO githi_llm_requests (
                    id, session_id, turn_seq, step, model, created_at,
                    request, response, ttft_ms, duration_ms,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    cost_usd
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                request_id,
                session_id,
                turn_seq,
                step,
                model,
                now,
                request,
                response,
                ttft_ms,
                duration_ms,
                input_tokens,
                output_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd
            )

        return request_id
