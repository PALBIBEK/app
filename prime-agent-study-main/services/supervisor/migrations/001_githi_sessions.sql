-- Migration 001: Githi Sessions Schema
-- Purpose: PostgreSQL persistence for Githi sessions, message history, and lease-based concurrency
-- Pattern: Following QM architecture (durable by default)

-- Sessions table: conversation metadata
CREATE TABLE IF NOT EXISTS githi_sessions (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    last_activity BIGINT NOT NULL,

    -- Session metadata as JSONB for flexibility
    metadata JSONB DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_githi_sessions_scope_activity
    ON githi_sessions(scope_id, last_activity DESC);

CREATE INDEX IF NOT EXISTS idx_githi_sessions_updated
    ON githi_sessions(updated_at DESC);

-- Session entries table: message history (durable message storage)
CREATE TABLE IF NOT EXISTS githi_session_entries (
    session_id TEXT NOT NULL REFERENCES githi_sessions(id) ON DELETE CASCADE,
    seq BIGSERIAL NOT NULL,
    type TEXT NOT NULL,  -- "user_message", "assistant_message", "tool_call", "tool_result"
    created_at BIGINT NOT NULL,
    payload JSONB NOT NULL,

    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_githi_entries_session_seq
    ON githi_session_entries(session_id, seq);

-- Session leases table: concurrency control (serialize turn processing)
-- Pattern: TTL-based leases prevent concurrent turn processing
CREATE TABLE IF NOT EXISTS githi_session_leases (
    session_id TEXT PRIMARY KEY REFERENCES githi_sessions(id) ON DELETE CASCADE,
    token TEXT NOT NULL,              -- Random UUID for this lease
    holder TEXT NOT NULL,             -- "turn", "compaction", "fork"
    acquired_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_githi_leases_expires
    ON githi_session_leases(expires_at);

-- Session tape table: event journal for replay and crash detection
-- Pattern: Record ALL events for debugging, recovery, audit
CREATE TABLE IF NOT EXISTS githi_session_tape (
    session_id TEXT NOT NULL REFERENCES githi_sessions(id) ON DELETE CASCADE,
    seq BIGSERIAL NOT NULL,
    kind TEXT NOT NULL,               -- "message", "context_event", "annotation"
    payload JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    entry_seq BIGINT,                 -- Link to session_entries.seq if applicable

    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_githi_tape_session_seq
    ON githi_session_tape(session_id, seq);

-- LLM requests table: track all model calls for cost, debugging, replay
CREATE TABLE IF NOT EXISTS githi_llm_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES githi_sessions(id) ON DELETE CASCADE,
    turn_seq BIGINT,
    step INTEGER NOT NULL,
    model TEXT NOT NULL,
    created_at BIGINT NOT NULL,

    -- Request/response data
    request JSONB,
    response JSONB,

    -- Performance metrics
    ttft_ms INTEGER,                  -- Time to first token
    duration_ms INTEGER,              -- Total duration

    -- Usage and cost
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    cost_usd NUMERIC(10, 4)
);

CREATE INDEX IF NOT EXISTS idx_githi_llm_session_turn
    ON githi_llm_requests(session_id, turn_seq, step);

CREATE INDEX IF NOT EXISTS idx_githi_llm_created
    ON githi_llm_requests(created_at DESC);

-- Cleanup function for expired leases
-- Run this periodically (e.g., every minute via cron)
CREATE OR REPLACE FUNCTION cleanup_expired_githi_leases()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM githi_session_leases
    WHERE expires_at < (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Comments for documentation
COMMENT ON TABLE githi_sessions IS 'Session metadata (conversation state)';
COMMENT ON TABLE githi_session_entries IS 'Message history (durable storage for all messages)';
COMMENT ON TABLE githi_session_leases IS 'Lease-based concurrency control (serialize turn processing)';
COMMENT ON TABLE githi_session_tape IS 'Event journal for replay, debugging, and crash detection';
COMMENT ON TABLE githi_llm_requests IS 'LLM call tracking for cost, debugging, and audit';

COMMENT ON COLUMN githi_session_leases.token IS 'Random UUID identifying this specific lease acquisition';
COMMENT ON COLUMN githi_session_leases.holder IS 'What acquired the lease: "turn", "compaction", "fork"';
COMMENT ON COLUMN githi_session_leases.expires_at IS 'Epoch milliseconds when lease auto-expires (TTL mechanism)';

COMMENT ON FUNCTION cleanup_expired_githi_leases IS 'Remove expired leases. Run periodically via cron.';
