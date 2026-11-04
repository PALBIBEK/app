"""Datasets of saved runs, and experiments that replay them on a chosen agent version.

A dataset belongs to one agent: each item is a run worth keeping (one that went wrong, or a
good one to protect), saved with what was asked and, optionally, what a good answer does.
An experiment replays every item of a dataset on one exact version (a release, or in later
phases a candidate), each in a fresh session, so versions are compared on the same inputs.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from prime_agent.db import Base
from prime_agent.db.models.harness_release import JSON_DOCUMENT


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> str:
    return str(uuid4())


class Dataset(Base):
    __tablename__ = "trace_datasets"
    __table_args__ = (UniqueConstraint("harness_id", "name", name="uq_trace_datasets_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    harness_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class DatasetItem(Base):
    """One saved run: the ask to replay, and what a good answer does (if someone said)."""

    __tablename__ = "trace_dataset_items"
    __table_args__ = (UniqueConstraint("dataset_id", "source_session_id", "source_run_id", name="uq_trace_dataset_items_run"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    input: Mapped[str] = mapped_column(Text, nullable=False)
    history: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """The messages the person sent earlier in the conversation, in order: replayed first, so
    the saved run is measured where it happened (not as a first message)."""
    expected: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What a good answer does, in a person's words (a judge's rubric can read it)."""
    source_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """How the saved run ended (``ok``, ``error`` …), for context."""
    held_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    """Kept from the optimizer: its conversation is never evidence a change is learnt from, so
    the gate can tell a change that generalises from one fitted to the runs it read."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Experiment(Base):
    """A dataset replayed on one exact version."""

    __tablename__ = "trace_experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    harness_id: Mapped[str] = mapped_column(String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    """What ran, as a person names it ("v3", "candidate from issue …")."""
    release_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    harness_version: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    composition_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    composition_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    """``queued``, ``running``, ``done``, ``failed`` or ``cancelled``."""
    trials: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    """Times each item is replayed: one answer per item measures noise (evaluation_service)."""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """Last progress; an experiment ``running`` with none for long was cut off (a restart)."""


class ExperimentRun(Base):
    """One item of the dataset, replayed in a fresh session."""

    __tablename__ = "trace_experiment_runs"
    __table_args__ = (UniqueConstraint("experiment_id", "item_id", "trial", name="uq_trace_experiment_runs_trial"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    experiment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_experiments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_dataset_items.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    trial: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    """Which replay of the item (0-based) of the experiment's ``trials``."""
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    """The product session it ran in (a session link the user owns)."""
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    """``pending``, ``running``, ``done``, ``needs_approval`` (stopped: nobody to approve),
    ``timed_out`` or ``failed``."""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Proposal(Base):
    """A change to an agent proposed from how its runs went wrong, and what became of it.

    It is drafted from the agent's issues (a reflection), published as a candidate that is not
    live, replayed on a dataset beside the version it would replace, and gated: a candidate
    that does worse on any saved run is refused. One that passes waits for a person, who sees
    the diff and the results; approving it makes the candidate, exactly as measured, the next
    version.
    """

    __tablename__ = "trace_proposals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    harness_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="drafting")
    """``drafting``, ``evaluating``, ``awaiting_approval``, ``refused`` (the gate said no),
    ``no_change`` (nothing to change), ``failed``, ``approved`` or ``rejected``."""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What the person asked to be addressed, if anything."""
    issue_key: Mapped[str | None] = mapped_column(String(600), nullable=True)
    """The issue the change is for, when it was started from one: the reflection reads it first."""
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_manifest: Mapped[dict] = mapped_column(JSON_DOCUMENT, nullable=False)
    candidate_manifest: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    edits: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """The itemized edits the reflection made (action, section, text, old, reason), in order."""
    skipped_edits: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """Edits it asked for that could not be applied, and why."""
    expected_outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What should improve, in the reflection's words: checked against the gate's result."""
    max_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    """Candidates the search may try (``ImprovementService._run``): one, or a few built on each other."""
    rounds: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """Each candidate tried: its parent, its edits, its replay, whether it was kept and why."""
    evidence: Mapped[list | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    candidate: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """The candidate's pin: composition id and digest, harness version, runtime."""
    baseline_experiment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    candidate_experiment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gate: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """The gate's decision: passed, and why, item by item."""
    release_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """The version it became, once approved."""
    watch: Mapped[dict | None] = mapped_column(JSON_DOCUMENT, nullable=True)
    """After approval, how its version does live against the one before it
    (``services.release_watch_service``): watching, held, rolled back or superseded."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AutoImprove(Base):
    """An agent's standing order to propose a change by itself when its failures pile up.

    Prime Agent's auto-refine (``pa-core/src/session_engine/auto_refine_trigger.rs``) is the
    model: gates first (on, a count since the last attempt, a cooldown that every attempt
    stamps, never two at once), then the refinement. Here the count is failing runs of the
    agent's biggest issue since the last attempt, and the refinement is a proposal: drafted,
    measured on ``dataset_id`` (with that issue's runs saved to it first) and gated, so a
    person still approves anything that goes live.
    """

    __tablename__ = "trace_auto_improve"

    harness_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("harnesses.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("trace_datasets.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    """Failing runs of one issue since the last attempt that start one."""
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=360)
    """Least time between attempts, whatever each one came to."""
    rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_proposal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    """What the last check found, in a sentence (started one, or why not)."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
