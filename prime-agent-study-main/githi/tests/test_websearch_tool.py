"""WebSearch tool -- red/green coverage for Claude Code feature parity."""

from __future__ import annotations

from datetime import datetime

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.websearch import websearch_tool
from githi.tools.websearch.prompt import WEB_SEARCH_TOOL_NAME, get_web_search_prompt
from githi.tools.websearch.format import format_search_output, make_output_from_blocks


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


class TestPrompt:
    def test_name(self):
        assert WEB_SEARCH_TOOL_NAME == "WebSearch"

    def test_prompt_requires_sources_section(self):
        text = get_web_search_prompt()
        assert "Sources:" in text
        assert "markdown hyperlinks" in text.lower() or "[Title](URL)" in text

    def test_prompt_includes_current_month_year(self):
        text = get_web_search_prompt()
        now = datetime.now()
        assert str(now.year) in text


class TestContract:
    def test_name_and_schema(self):
        tool = websearch_tool()
        assert tool.name == "WebSearch"
        props = tool.parameters["properties"]
        assert "query" in props
        assert "allowed_domains" in props
        assert "blocked_domains" in props
        assert tool.parameters["required"] == ["query"]
        assert "search" in tool.description.lower()

    def test_empty_query_is_error(self, ctx):
        assert websearch_tool().run({"query": ""}, ctx).is_error
        assert websearch_tool().run({}, ctx).is_error

    def test_short_query_is_error(self, ctx):
        result = websearch_tool().run({"query": "a"}, ctx)
        assert result.is_error

    def test_cannot_combine_allowed_and_blocked(self, ctx):
        result = websearch_tool().run({
            "query": "rust async",
            "allowed_domains": ["rust-lang.org"],
            "blocked_domains": ["spam.com"],
        }, ctx)
        assert result.is_error
        assert "Cannot specify both" in result.text


class TestFormat:
    def test_formats_hits_and_text_with_sources_reminder(self):
        blocks = [
            {"type": "text", "text": "Here is what I found."},
            {"type": "server_tool_use", "id": "s1"},
            {
                "type": "web_search_tool_result",
                "tool_use_id": "s1",
                "content": [
                    {"title": "Rust Book", "url": "https://doc.rust-lang.org/book/"},
                    {"title": "Async Book", "url": "https://rust-lang.github.io/async-book/"},
                ],
            },
            {"type": "text", "text": "Rust has native async/await."},
        ]
        output = make_output_from_blocks(blocks, query="rust async", duration_seconds=1.5)
        assert output["query"] == "rust async"
        assert output["durationSeconds"] == 1.5
        assert any(isinstance(r, dict) and r.get("content") for r in output["results"])

        text = format_search_output(output)
        assert 'Web search results for query: "rust async"' in text
        assert "Rust Book" in text
        assert "REMINDER" in text
        assert "Sources" in text or "sources" in text.lower()

    def test_search_error_block_becomes_message(self):
        blocks = [{
            "type": "web_search_tool_result",
            "tool_use_id": "s1",
            "content": {"error_code": "max_uses_exceeded"},
        }]
        output = make_output_from_blocks(blocks, query="x", duration_seconds=0.1)
        assert any(
            isinstance(r, str) and "max_uses_exceeded" in r
            for r in output["results"]
        )


class TestCall:
    def test_uses_injected_search_backend(self, ctx):
        def search(query, *, allowed_domains=None, blocked_domains=None):
            assert query == "python 3.13"
            assert allowed_domains == ["python.org"]
            return {
                "query": query,
                "results": [
                    "Python 3.13 released.",
                    {
                        "tool_use_id": "t1",
                        "content": [
                            {"title": "What's New", "url": "https://docs.python.org/3.13/whatsnew/"},
                        ],
                    },
                ],
                "durationSeconds": 0.2,
            }

        ctx.extra["web_search"] = search
        result = websearch_tool().run(
            {"query": "python 3.13", "allowed_domains": ["python.org"]}, ctx)
        assert not result.is_error
        assert "What's New" in result.text
        assert "REMINDER" in result.text
        assert result.details.get("query") == "python 3.13"
