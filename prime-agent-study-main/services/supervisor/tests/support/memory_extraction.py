"""A model for Tier 1 extraction in tests: the real ModelExtractor over a scripted endpoint."""

from __future__ import annotations

import json

import httpx

from supervisor.memory_service.model_extractor import ModelExtractor


class ScriptedModel:
    """Answers chat completions with ``answer`` and keeps the requests it got."""

    def __init__(self, answer: str = "SENSITIVITY: ordinary\n- Prefers pytest for all tests") -> None:
        self.answer = answer
        self.requests: list[dict[str, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": self.answer}}]})

    def extractor(self) -> ModelExtractor:
        return ModelExtractor("http://model.test/v1", model="test-model", transport=httpx.MockTransport(self))


def scripted_extractor(answer: str = "SENSITIVITY: ordinary\n- Prefers pytest for all tests") -> ModelExtractor:
    return ScriptedModel(answer).extractor()
