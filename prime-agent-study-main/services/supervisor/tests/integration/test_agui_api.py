"""A githi session as an AG-UI agent, over HTTP, on Temporal: a run asked for and streamed,
the view the agent drew as an A2UI activity, and the session followed and resumed.

Every event is parsed by the official AG-UI SDK, as an AG-UI client would."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import pydantic
import pytest
from ag_ui.core import Event
from httpx import AsyncClient

from supervisor.agui import A2UI_ACTIVITY, A2UI_OPERATIONS
from supervisor.genui import CATALOG_ID
from supervisor.temporal.worker import session_worker
from tests.support.api import supervisor_api
from tests.support.browser import browser_session
from tests.support.githi_env import Env
from tests.support.scripted import ScriptedGateway

pytestmark = pytest.mark.asyncio(loop_scope="session")

EVENTS = pydantic.TypeAdapter(Event)
REPORT = [
    {"version": "v0.9", "createSurface": {"surfaceId": "prices", "catalogId": CATALOG_ID}},
    {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "prices",
            "components": [
                {"id": "root", "component": "Column", "children": ["title", "body"]},
                {"id": "title", "component": "Text", "text": "Paper towels", "variant": "h2"},
                {"id": "body", "component": "Text", "text": "Cheapest: **£3**, at A"},
            ],
        },
    },
]


ASK = [
    {"version": "v0.9", "createSurface": {"surfaceId": "which", "catalogId": CATALOG_ID}},
    {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "which",
            "components": [
                {"id": "root", "component": "Column", "children": ["pick", "go"]},
                {"id": "pick", "component": "ChoicePicker", "options": [{"label": "A, £3", "value": "a"}, {"label": "B, £4", "value": "b"}], "value": {"path": "/pick"}},
                {"id": "go", "component": "Button", "child": "go_label", "action": {"event": {"name": "chosen", "context": {"pick": {"path": "/pick"}}}}},
                {"id": "go_label", "component": "Text", "text": "Add to basket"},
            ],
        },
    },
]


def run_input(sid: str, run_id: str, text: str) -> dict[str, Any]:
    """An AG-UI ``RunAgentInput``, as a client sends it."""
    return {
        "threadId": sid,
        "runId": run_id,
        "messages": [{"id": f"m-{run_id}", "role": "user", "content": text}],
        "tools": [],
        "context": [],
        "state": None,
        "forwardedProps": None,
    }


def clicked(sid: str, run_id: str, pick: list[str], *, button: str = "go", name: str = "chosen") -> dict[str, Any]:
    """A run that sends a person's click, as an AG-UI client with A2UI sends it."""
    action = {"name": name, "surfaceId": "which", "sourceComponentId": button, "timestamp": "2026-11-05T10:00:00Z", "context": {"pick": pick}}
    return {**run_input(sid, run_id, "unused"), "messages": [], "forwardedProps": {"a2uiAction": {"userAction": action}}}


def frames(body: str) -> list[tuple[int | None, dict[str, Any]]]:
    """An SSE body's events: (its id, if it has one; the event, checked by the SDK)."""
    out: list[tuple[int | None, dict[str, Any]]] = []
    for frame in body.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line and not line.startswith(":"))
        if "data" in fields:
            EVENTS.validate_json(fields["data"])
            out.append((int(fields["id"]) if "id" in fields else None, json.loads(fields["data"])))
    return out


async def _run(api: AsyncClient, sid: str, run_id: str, text: str) -> list[tuple[int | None, dict[str, Any]]]:
    response = await api.post(f"/v1/sessions/{sid}/agui", json=run_input(sid, run_id, text))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return frames(response.text)


async def test_a_run_asked_for_streams_to_its_end_with_the_view_it_drew(
    env: Env, model: Callable[..., ScriptedGateway]
) -> None:
    model(("show_ui", {"messages": REPORT}))
    state = await env.state()
    state.workflows = env.sessions  # the API drives sessions through their workflows
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await browser_session(env, state)  # the browser tool set offers show_ui
        run_id = f"client-{uuid.uuid4().hex[:6]}"
        streamed = await _run(api, sid, run_id, "compare paper towel prices")
        again = await _run(api, sid, run_id, "compare paper towel prices")  # the same run, sent twice

    events = [e for _, e in streamed]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED", kinds
    assert events[0]["runId"] == events[-1]["runId"] == run_id, "the run keeps the id the client gave it"
    assert events[0]["threadId"] == sid
    user = next(e for e in events if e["type"] == "TEXT_MESSAGE_START" and e.get("role") == "user")
    assert next(e["delta"] for e in events if e.get("messageId") == user["messageId"] and "delta" in e) == "compare paper towel prices"
    call = next(e for e in events if e["type"] == "TOOL_CALL_START")
    assert call["toolCallName"] == "show_ui"
    assert json.loads(next(e["delta"] for e in events if e["type"] == "TOOL_CALL_ARGS")) == {"messages": REPORT}
    result = next(e for e in events if e["type"] == "TOOL_CALL_RESULT")
    assert result["toolCallId"] == call["toolCallId"] and "prices" in result["content"]
    (activity,) = [e for e in events if e["type"] == "ACTIVITY_SNAPSHOT"]
    assert (activity["activityType"], activity["content"]) == (A2UI_ACTIVITY, {A2UI_OPERATIONS: REPORT})
    assert kinds.index("ACTIVITY_SNAPSHOT") > kinds.index("TOOL_CALL_RESULT"), "drawn once it was accepted"
    assert [e["type"] for _, e in again] == kinds, "sent again, the run is streamed again, not run twice"


async def test_a_followed_session_resumes_after_the_last_event_it_had(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    model()
    state = await env.state()
    state.workflows = env.sessions
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await env.create(state)
        await _run(api, sid, "r1", "hello")
        assert (await api.delete(f"/v1/sessions/{sid}")).status_code == 204  # ended: the stream ends too
        whole = frames((await api.get(f"/v1/sessions/{sid}/agui")).text)
        ids = [seq for seq, _ in whole if seq is not None]
        middle = ids[len(ids) // 2]
        rest = frames((await api.get(f"/v1/sessions/{sid}/agui", headers={"Last-Event-ID": str(middle)})).text)

    assert ids == sorted(ids) and len(set(ids)) == len(ids), "one id per journalled event, in order"
    after = whole[[seq for seq, _ in whole].index(middle) + 1 :]
    assert rest == after, "a reconnect sends what came after, nothing twice"
    assert {"RUN_STARTED", "RUN_FINISHED"} <= {e["type"] for _, e in whole}


async def test_a_persons_choice_reaches_the_agent_once_per_drawing(env: Env, model: Callable[..., ScriptedGateway]) -> None:
    gateway = model(("show_ui", {"messages": ASK}))
    state = await env.state()
    state.workflows = env.sessions
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await browser_session(env, state)
        await _run(api, sid, "r-ask", "which shop?")
        gateway.calls.clear()  # it asks once; the answer is the next run's message
        answered = await api.post(f"/v1/sessions/{sid}/agui", json=clicked(sid, "r-click", ["a"]))
        again = await api.post(f"/v1/sessions/{sid}/agui", json=clicked(sid, "r-click-again", ["a"]))
        otherwise = await api.post(f"/v1/sessions/{sid}/agui", json=clicked(sid, "r-other", ["b"]))
        forged = await api.post(f"/v1/sessions/{sid}/agui", json=clicked(sid, "r-forged", ["a"], name="pay"))

    assert answered.status_code == 200, answered.text
    events = [e for _, e in frames(answered.text)]
    assert (events[0]["type"], events[0]["runId"], events[-1]["type"]) == ("RUN_STARTED", "r-click", "RUN_FINISHED")
    told = [m.content for m in gateway.seen[-1] if m.role == "user"][-1]
    assert told.startswith("UI action: ") and json.loads(told.removeprefix("UI action: ")) == {
        "name": "chosen", "surfaceId": "which", "sourceComponentId": "go", "context": {"pick": ["a"]}
    }, "the agent reads what was chosen, from where"
    assert again.status_code == 200 and [e["type"] for _, e in frames(again.text)] == [e["type"] for e in events], "the same click is the same message"
    assert sum(1 for seen in gateway.seen for m in seen[-1:] if m.content == told) == 1, "and the agent is told it once"
    assert otherwise.status_code == 409 and "was answered already" in otherwise.text
    assert forged.status_code == 422 and "button 'go' sends 'chosen', not 'pay'" in forged.text


@pytest.mark.parametrize(
    ("body", "status", "said"),
    [
        (lambda sid: run_input("another-session", "r1", "hi"), 400, "threadId is 'another-session'"),
        (lambda sid: {**run_input(sid, "r1", "hi"), "messages": []}, 422, "no user message"),
        (lambda sid: {"threadId": sid}, 422, "runId"),
    ],
)
async def test_a_run_that_cannot_be_asked_for_is_refused(
    env: Env, body: Callable[[str], dict[str, Any]], status: int, said: str
) -> None:
    state = await env.state()
    state.workflows = env.sessions
    async with session_worker(env.client, state, task_queue=env.queue), supervisor_api(state) as api:
        sid = await env.create(state)
        response = await api.post(f"/v1/sessions/{sid}/agui", json=body(sid))

    assert response.status_code == status and said in response.text, response.text
