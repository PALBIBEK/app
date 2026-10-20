"""Compaction works on any model window, with the shipped default profile.

The default profile's sizes are absolute (reserve 16,384, keep 20,000 tokens).
Unscaled, on a small window the limit went negative and the "recent" span kept
everything, so compaction never helped; and a newest message bigger than the
whole budget was always kept, leaving the conversation over the window.
"""

from __future__ import annotations

from typing import Any

import pytest

from supervisor.catalog.core.types import ArtifactStatus, MemoryProfile
from supervisor.seams.providers.memory.basic_compaction import BasicCompactionProvider
from supervisor.temporal.context_memory import _Binding, _recent

DEFAULT = MemoryProfile(
    name="default", semver="1.0.0", digest="", protocol_version=1, status=ArtifactStatus.OFFERED
)


def _binding(profile: MemoryProfile = DEFAULT) -> _Binding:
    return _Binding(profile, BasicCompactionProvider(gateway=None), context=None, gateway=None, model=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("window", [3_000, 8_192, 32_000, 200_000])
def test_the_limit_and_the_kept_span_fit_the_window(window: int) -> None:
    binding = _binding()
    assert 0 < binding.limit(window) < window
    assert binding.keep_recent(window) < binding.limit(window)
    assert binding.reserve(window) + binding.limit(window) <= window


def test_large_windows_keep_the_profiles_own_sizes() -> None:
    binding = _binding()
    assert binding.reserve(200_000) == DEFAULT.reserve_tokens
    assert binding.keep_recent(200_000) == DEFAULT.keep_recent_tokens


def _message(role: str, words: int) -> dict[str, Any]:
    return {"role": role, "content": "word " * words}


def test_an_oversized_newest_message_is_summarised_not_kept() -> None:
    memory = BasicCompactionProvider(gateway=None)  # type: ignore[arg-type]
    huge_reply = _message("assistant", 5_000)
    assert _recent([_message("user", 10), huge_reply], keep_tokens=500, memory=memory) == []


def test_recent_messages_within_the_budget_are_kept() -> None:
    memory = BasicCompactionProvider(gateway=None)  # type: ignore[arg-type]
    messages = [_message("user", 3_000), _message("assistant", 20), _message("user", 20)]
    assert _recent(messages, keep_tokens=500, memory=memory) == messages[1:]
