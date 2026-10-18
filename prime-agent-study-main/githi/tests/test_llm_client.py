"""The LLM client.

Every test here uses a fake transport -- no network, no key, no cost. The
client's job is translation (our types <-> the wire) plus retry, and both are
fully testable offline. A test suite that needs a live provider is one nobody
runs.
"""

from __future__ import annotations

import httpx
import pytest

from githi.errors import ConfigError, LLMError
from githi.llm.client import LLMClient
from githi.types import AssistantMessage, SystemMessage, UserMessage


def _response(content="hi", tool_calls=None, usage=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 3},
    }


def _client(handler, **kw):
    transport = httpx.MockTransport(handler)
    return LLMClient(api_key="test-key", model="anthropic/claude-haiku-4.5",
                     http_client=httpx.Client(transport=transport), **kw)


class TestCompletion:
    def test_returns_assistant_text(self):
        client = _client(lambda r: httpx.Response(200, json=_response("hello")))
        reply = client.complete([UserMessage("hi")], tools=[])
        assert isinstance(reply.message, AssistantMessage)
        assert reply.message.content == "hello"

    def test_parses_tool_calls(self):
        calls = [{"id": "c1", "type": "function",
                  "function": {"name": "bash", "arguments": '{"command": "ls"}'}}]
        client = _client(lambda r: httpx.Response(200, json=_response("", calls)))
        reply = client.complete([UserMessage("hi")], tools=[])
        assert reply.message.tool_calls[0].name == "bash"
        assert reply.message.tool_calls[0].arguments == {"command": "ls"}

    def test_reports_usage(self):
        client = _client(lambda r: httpx.Response(200, json=_response()))
        assert client.complete([UserMessage("hi")], tools=[]).usage.input_tokens == 10

    def test_sends_system_message_and_tools(self):
        seen = {}

        def handler(request):
            seen.update(__import__("json").loads(request.content))
            return httpx.Response(200, json=_response())

        client = _client(handler)
        client.complete([SystemMessage("rules"), UserMessage("go")],
                        tools=[{"type": "function",
                                "function": {"name": "bash", "description": "d",
                                             "parameters": {"type": "object"}}}])
        assert seen["messages"][0]["role"] == "system"
        assert seen["tools"][0]["function"]["name"] == "bash"

    def test_omits_tools_key_when_no_tools(self):
        """An empty tools array is rejected by some providers."""
        seen = {}

        def handler(request):
            seen.update(__import__("json").loads(request.content))
            return httpx.Response(200, json=_response())

        _client(handler).complete([UserMessage("hi")], tools=[])
        assert "tools" not in seen


class TestReasoningRequest:
    def test_thinking_level_becomes_openrouter_reasoning_effort(self):
        """Pi's openrouter thinkingFormat: reasoning: { effort }."""
        from githi.types import ThinkingLevel
        seen = {}

        def handler(request):
            seen.update(__import__("json").loads(request.content))
            return httpx.Response(200, json=_response())

        _client(handler).complete([UserMessage("hi")], tools=[],
                                  thinking_level=ThinkingLevel.HIGH)
        assert seen["reasoning"] == {"effort": "high"}

    def test_off_omits_the_reasoning_object(self):
        from githi.types import ThinkingLevel
        seen = {}

        def handler(request):
            seen.update(__import__("json").loads(request.content))
            return httpx.Response(200, json=_response())

        _client(handler).complete([UserMessage("hi")], tools=[],
                                  thinking_level=ThinkingLevel.OFF)
        assert "reasoning" not in seen

    def test_blocking_response_captures_reasoning_content(self):
        def handler(request):
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "content": "ok",
                    "reasoning_content": "because",
                }, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        reply = _client(handler).complete([UserMessage("hi")], tools=[])
        assert reply.message.thinking == "because"
        assert reply.message.thinking_signature == "reasoning_content"


class TestErrors:
    def test_missing_api_key_is_a_config_error(self):
        with pytest.raises(ConfigError):
            LLMClient(api_key="", model="m")

    def test_http_error_becomes_llm_error(self):
        client = _client(lambda r: httpx.Response(400, json={"error": {"message": "bad"}}),
                         max_retries=0)
        with pytest.raises(LLMError, match="bad"):
            client.complete([UserMessage("hi")], tools=[])

    def test_malformed_response_becomes_llm_error(self):
        client = _client(lambda r: httpx.Response(200, json={"nonsense": True}), max_retries=0)
        with pytest.raises(LLMError):
            client.complete([UserMessage("hi")], tools=[])


class TestRetry:
    def test_retries_on_429_then_succeeds(self):
        """Rate limits are routine and transient; failing the whole run on the
        first one would waste everything done so far."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": {"message": "slow down"}})
            return httpx.Response(200, json=_response("recovered"))

        client = _client(handler, max_retries=2, retry_base_delay=0)
        assert client.complete([UserMessage("hi")], tools=[]).message.content == "recovered"
        assert calls["n"] == 2

    def test_retries_on_500(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return (httpx.Response(500, text="boom") if calls["n"] == 1
                    else httpx.Response(200, json=_response()))

        client = _client(handler, max_retries=2, retry_base_delay=0)
        client.complete([UserMessage("hi")], tools=[])
        assert calls["n"] == 2

    def test_does_not_retry_client_errors(self):
        """A 400 means the request is wrong; sending it again wastes money and
        time to get the identical answer."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(400, json={"error": {"message": "bad request"}})

        with pytest.raises(LLMError):
            _client(handler, max_retries=3, retry_base_delay=0).complete(
                [UserMessage("hi")], tools=[])
        assert calls["n"] == 1

    def test_gives_up_after_max_retries(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, json={"error": {"message": "nope"}})

        with pytest.raises(LLMError):
            _client(handler, max_retries=2, retry_base_delay=0).complete(
                [UserMessage("hi")], tools=[])
        assert calls["n"] == 3          # initial attempt plus two retries
