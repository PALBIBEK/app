"""Prompt caching.

Compaction bounds how large the transcript gets; caching makes re-sending
what remains cheap. They attack the same cost from opposite ends and neither
substitutes for the other.
"""

from __future__ import annotations

import json

import pytest

from githi.llm.caching import (
    MIN_CACHEABLE_CHARS,
    apply_cache_breakpoints,
    supports_caching,
)


def _wire(system: str, n_messages: int = 1) -> list[dict]:
    msgs = [{"role": "system", "content": system}]
    msgs += [{"role": "user", "content": f"message {i}"} for i in range(n_messages)]
    return msgs


BIG = "You are an agent. " * 500


class TestModelGating:
    @pytest.mark.parametrize("model", [
        "anthropic/claude-haiku-4.5", "claude-sonnet-4.5", "openrouter/anthropic/claude-opus-4",
    ])
    def test_enabled_for_anthropic(self, model):
        assert supports_caching(model) is True

    @pytest.mark.parametrize("model", [
        "openai/gpt-4o", "google/gemini-2.5-pro", "meta-llama/llama-3-70b",
    ])
    def test_disabled_elsewhere(self, model):
        """Fails closed: a provider that rejects an unknown field would turn a
        cost optimisation into an outage on every call."""
        assert supports_caching(model) is False


class TestBreakpoints:
    def test_system_prompt_is_marked(self):
        out = apply_cache_breakpoints(_wire(BIG), tools_chars=0)
        assert isinstance(out[0]["content"], list)
        assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_rolling_breakpoint_on_last_message(self):
        """Turn N then reads turns 1..N-1 from cache instead of paying full
        price for them again."""
        out = apply_cache_breakpoints(_wire(BIG, 3), tools_chars=0)
        assert "cache_control" in json.dumps(out[-1])

    def test_at_most_four_breakpoints(self):
        """Anthropic's hard limit; exceeding it is a 400."""
        out = apply_cache_breakpoints(_wire(BIG, 40), tools_chars=0)
        assert json.dumps(out).count("cache_control") <= 4

    def test_tool_schemas_count_toward_the_minimum(self):
        """The cached prefix is tools + system, not system alone. Measuring
        only the system prompt skips caching on exactly the setups that would
        benefit -- a modest prompt with many large tool schemas."""
        small_system = "short prompt"
        assert len(small_system) < MIN_CACHEABLE_CHARS
        out = apply_cache_breakpoints(_wire(small_system), tools_chars=MIN_CACHEABLE_CHARS)
        assert "cache_control" in json.dumps(out[0])

    def test_tiny_prefix_is_left_alone(self):
        """Below the provider minimum a breakpoint is ignored and billed as a
        cache write at 1.25x -- strictly worse than not marking it."""
        out = apply_cache_breakpoints(_wire("short"), tools_chars=0)
        assert isinstance(out[0]["content"], str)

    def test_content_is_preserved_exactly(self):
        out = apply_cache_breakpoints(_wire(BIG, 1), tools_chars=0)
        assert out[0]["content"][0]["text"] == BIG
        assert out[-1]["content"][0]["text"] == "message 0"

    def test_empty_content_is_not_marked(self):
        """An assistant tool-call message has null content -- there is no
        block to attach a breakpoint to."""
        msgs = _wire(BIG) + [{"role": "assistant", "content": "", "tool_calls": []}]
        assert "cache_control" not in json.dumps(apply_cache_breakpoints(msgs, 0)[-1])

    def test_no_messages_beyond_system(self):
        out = apply_cache_breakpoints([{"role": "system", "content": BIG}], tools_chars=0)
        assert len(out) == 1 and "cache_control" in json.dumps(out)

    def test_does_not_mutate_the_input(self):
        """The caller keeps its own transcript; caching is a wire-format
        concern that must not leak back into it."""
        original = _wire(BIG, 2)
        snapshot = json.dumps(original)
        apply_cache_breakpoints(original, tools_chars=0)
        assert json.dumps(original) == snapshot
