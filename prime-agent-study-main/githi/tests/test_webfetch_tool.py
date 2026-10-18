"""WebFetch tool contract -- validation, redirect messaging, prompt apply."""

from __future__ import annotations

import httpx
import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.webfetch import webfetch_tool
from githi.tools.webfetch.prompt import DESCRIPTION, WEB_FETCH_TOOL_NAME
from githi.tools.webfetch.utils import clear_web_fetch_cache


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_web_fetch_cache()
    yield
    clear_web_fetch_cache()


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestContract:
    def test_name_and_schema(self):
        tool = webfetch_tool()
        assert tool.name == WEB_FETCH_TOOL_NAME == "WebFetch"
        assert "url" in tool.parameters["properties"]
        assert "prompt" in tool.parameters["properties"]
        assert set(tool.parameters["required"]) == {"url", "prompt"}
        assert "Fetches content" in tool.description or "fetch" in tool.description.lower()
        assert "authenticated" in tool.description.lower()

    def test_description_includes_usage_notes(self):
        assert "HTTPS" in DESCRIPTION or "https" in DESCRIPTION.lower()
        assert "cache" in DESCRIPTION.lower() or "15-minute" in DESCRIPTION

    def test_missing_url_is_error(self, ctx):
        result = webfetch_tool().run({"prompt": "summarize"}, ctx)
        assert result.is_error

    def test_missing_prompt_is_error(self, ctx):
        result = webfetch_tool().run({"url": "https://example.com/"}, ctx)
        assert result.is_error

    def test_invalid_url_is_error(self, ctx):
        result = webfetch_tool().run(
            {"url": "not-a-url", "prompt": "x"}, ctx)
        assert result.is_error
        assert "Invalid URL" in result.text or "invalid" in result.text.lower()


class TestFetchAndApply:
    def test_fetches_and_applies_prompt_via_injected_model(self, ctx):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"<html><body><h1>Docs</h1><p>Version 9</p></body></html>",
                headers={"content-type": "text/html"},
            )

        def apply_prompt(prompt, markdown, *, is_preapproved):
            assert "Version 9" in markdown or "Docs" in markdown
            assert prompt == "What version?"
            return "Version is 9"

        ctx.extra["http_client"] = _client(handler)
        ctx.extra["skip_web_fetch_preflight"] = True
        ctx.extra["apply_prompt"] = apply_prompt

        result = webfetch_tool().run(
            {"url": "https://example.com/docs", "prompt": "What version?"}, ctx)
        assert not result.is_error
        assert "Version is 9" in result.text
        assert result.details.get("code") == 200
        assert "url" in result.details

    def test_preapproved_short_markdown_skips_model(self, ctx):
        md = "# Python docs\n\nHello from preapproved."

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=md.encode(),
                headers={"content-type": "text/markdown"},
            )

        called = {"n": 0}

        def apply_prompt(*a, **k):
            called["n"] += 1
            return "should not run"

        ctx.extra["http_client"] = _client(handler)
        ctx.extra["skip_web_fetch_preflight"] = True
        ctx.extra["apply_prompt"] = apply_prompt

        result = webfetch_tool().run(
            {"url": "https://docs.python.org/3/", "prompt": "summarize"}, ctx)
        assert not result.is_error
        assert "Hello from preapproved" in result.text
        assert called["n"] == 0

    def test_cross_host_redirect_instructs_retry(self, ctx):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                301, headers={"Location": "https://other.com/target"})

        ctx.extra["http_client"] = _client(handler)
        ctx.extra["skip_web_fetch_preflight"] = True
        ctx.extra["apply_prompt"] = lambda *a, **k: "unused"

        result = webfetch_tool().run(
            {"url": "https://example.com/old", "prompt": "get title"}, ctx)
        assert not result.is_error
        assert "REDIRECT DETECTED" in result.text
        assert "https://other.com/target" in result.text
        assert 'prompt: "get title"' in result.text
        assert result.details.get("code") == 301
