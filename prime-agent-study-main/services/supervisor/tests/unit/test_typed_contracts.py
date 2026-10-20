"""Bugs the type checker found, pinned by behaviour.

Each test fails on the code as it was before the fix: the checker saw a call that
could not work, and these show what a user would have hit.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from supervisor.catalog.core.errors import DigestMissing
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.config import TemporalSettings
from supervisor.harness import BudgetSpec
from supervisor.policy.budget.tracker import BudgetTracker


def test_temporal_settings_default_to_a_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """With slots, the defaults read as ``cls.address`` were descriptors, not strings."""
    for name in ("TEMPORAL_ADDRESS", "TEMPORAL_NAMESPACE", "TEMPORAL_TASK_QUEUE"):
        monkeypatch.delenv(name, raising=False)
    settings = TemporalSettings.from_env()
    assert (settings.address, settings.namespace, settings.task_queue) == (
        "localhost:7233",
        "default",
        "supervisor-sessions",
    )


def test_a_composition_naming_an_unknown_artifact_is_digest_missing(tmp_path: Path) -> None:
    """DigestMissing was called positionally and raised a TypeError instead."""
    (tmp_path / "compositions").mkdir()
    (tmp_path / "compositions" / "c.json").write_text(
        json.dumps(
            {
                "name": "c",
                "semver": "1.0.0",
                "harness_version": "githi-v1",
                "prompt_pack": {"id": "no-such-prompt"},
                "tool_set": {"id": "no-such-tools"},
            }
        )
    )
    with pytest.raises(DigestMissing) as caught:
        load_composition_catalog(tmp_path)
    assert caught.value.detail["artifact_id"] == "no-such-prompt"


def test_a_manifest_budget_limits_cost_exactly() -> None:
    """The manifest states USD as a float; the tracker compares money as Decimal."""
    tracker = BudgetTracker(session_factory=None)  # type: ignore[arg-type]
    tracker.configure("s", BudgetSpec(max_cost_per_session=0.3))
    at_limit = tracker._evaluate("s", cost=Decimal("0.1") * 3, tokens=0)
    assert at_limit.max_cost_usd == Decimal("0.3") and not at_limit.exceeded
    over = tracker._evaluate("s", cost=Decimal("0.31"), tokens=0)
    assert over.exceeded and over.exceeded_reason is not None


async def test_a_stored_version_on_a_retired_runtime_does_not_stop_boot(tmp_path: Path) -> None:
    from supervisor.db.engine import open_db
    from supervisor.db.models import HarnessVersionRow
    from supervisor.harness import HarnessCatalog
    from supervisor.harness.repository import hydrate_harness_catalog

    async with open_db(f"sqlite+aiosqlite:///{tmp_path / 'h.db'}") as (_, factory):
        async with factory() as db:
            db.add(HarnessVersionRow(harness_version="my-pi", protocol_version=1, runtime="harness",
                                     manifest={"harness_version": "my-pi", "protocol_version": 1,
                                               "runtime": "harness", "requires_sandbox": False,
                                               "recovery": {"default": "fail"}}))
            await db.commit()
        catalog = HarnessCatalog({})
        assert await hydrate_harness_catalog(catalog, factory) == 0
