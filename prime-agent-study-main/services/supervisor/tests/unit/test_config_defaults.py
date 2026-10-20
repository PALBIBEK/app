"""Settings read from an empty environment get their documented defaults."""

from __future__ import annotations

import pytest

from supervisor.config import TemporalSettings


def test_temporal_settings_default_when_the_environment_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("TEMPORAL_ADDRESS", "TEMPORAL_NAMESPACE", "TEMPORAL_TASK_QUEUE", "TEMPORAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Used to be the dataclass's slot descriptors, not these strings.
    assert TemporalSettings.from_env() == TemporalSettings(
        "localhost:7233", "default", "supervisor-sessions", None
    )
