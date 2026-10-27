"""Traces over HTTP: runs across sessions with filters, one session's runs, one run's tree,
one span's content, and who may see them.

Runs the API in-process on SQLite. Sessions and their journals are inserted as rows, so
no worker runs; a run's rollup row is filled in the first time it is listed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from supervisor.agent.events import RunEnd, RunStart, TurnEnd
from supervisor.db.models import RunTraceRow
from supervisor.journal.reader import JournalReader
from supervisor.traces.store import record_run
from tests.support.trace_api import ALICE, BOB, T1, T2, Api, list_traces


async def test_runs_are_listed_newest_first_and_only_to_whoever_may_see_their_session(api: Api) -> None:
    mine = await api.session(owner="alice")
    bobs = await api.session(owner="bob")
    other_tenant = await api.session(tenant="t2", owner=None)
    await api.run(mine, "r1", "build it", start=1_000)
    await api.run(mine, "r2", "build it again", start=5_000)
    await api.run(bobs, "r3", "bob's build", start=3_000)
    await api.run(other_tenant, "r4", "elsewhere", start=4_000)

    alice = await list_traces(api, ALICE)
    assert [i["run_id"] for i in alice["items"]] == ["r2", "r1"]
    first = alice["items"][0]
    assert first["status"] == "ok" and first["input"] == "build it again" and first["output"] == "answered build it again"
    assert first["cost"] == 0.03 and first["tool_calls"] == 1 and first["tools"] == {"bash": 1}
    assert first["composition_id"] == "githi-v1" and len(first["trace_id"]) == 32

    assert [i["run_id"] for i in (await list_traces(api, T1))["items"]] == ["r2", "r3", "r1"]
    assert [i["run_id"] for i in (await list_traces(api, BOB))["items"]] == ["r3"]
    assert [i["run_id"] for i in (await list_traces(api, T2))["items"]] == ["r4"]


async def test_filters_and_pages(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "ok", "fine", start=1_000)
    await api.run(sid, "broke", "tool fails", start=2_000, tool_ok=False)
    await api.run(sid, "said-no", "was denied", start=3_000, denied=True)
    await api.run(sid, "going", "still going", start=4_000, ended=False)

    def ids(page: dict[str, Any]) -> list[str]:
        return [i["run_id"] for i in page["items"]]

    assert ids(await list_traces(api, ALICE, errors="true")) == ["broke"]
    assert ids(await list_traces(api, ALICE, denied="true")) == ["said-no"]
    assert ids(await list_traces(api, ALICE, status="running")) == ["going"]
    assert ids(await list_traces(api, ALICE, status=["ok", "running"])) == ["going", "said-no", "broke", "ok"]
    assert ids(await list_traces(api, ALICE, q="DENIED")) == ["said-no"]
    assert ids(await list_traces(api, ALICE, session_id=uuid4().hex)) == []

    page1 = await list_traces(api, ALICE, limit=2)
    assert ids(page1) == ["going", "said-no"] and page1["next_before_ms"] == 3_000
    page2 = await list_traces(api, ALICE, limit=2, before_ms=page1["next_before_ms"])
    assert ids(page2) == ["broke", "ok"] and page2["next_before_ms"] is None

    r = await api.client.get("/v1/traces", params={"status": "sideways"}, headers=ALICE)
    assert r.status_code == 422


async def test_a_session_replays_its_runs_and_an_ended_session_has_no_running_runs(api: Api) -> None:
    sid = await api.session(status="terminated")
    await api.run(sid, "first", "one", start=1_000)
    await api.run(sid, "cut-off", "two", start=2_000, ended=False)

    r = await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(i["run_id"], i["status"]) for i in items] == [("first", "ok"), ("cut-off", "interrupted")]
    assert (await api.client.get(f"/v1/sessions/{sid}/traces", headers=BOB)).status_code == 404


async def test_a_run_is_a_tree_and_a_span_opens_with_its_content(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "r1", "make it", start=1_000)

    r = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["agent"] == "githi-v1" and body["rollup"]["status"] == "ok"
    assert body["summary"] == "Ran 1 command; answered."
    assert [s["kind"] for s in body["spans"]] == ["agent", "step", "llm", "tool", "step", "llm"]
    assert all("detail" not in s for s in body["spans"])
    tool = next(s for s in body["spans"] if s["kind"] == "tool")
    assert tool["preview"] == "$ make" and tool["attrs"]["exit_code"] == 0

    detail = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace/spans/{tool['id']}", headers=ALICE)
    assert detail.status_code == 200
    assert detail.json()["detail"]["args"] == {"command": "make"}
    assert detail.json()["detail"]["result"]["payload"]["stdout"] == "ok"

    raw = await api.client.get(f"/v1/sessions/{sid}/runs/r1/trace/events", headers=ALICE)
    assert [e["event_type"] for e in raw.json()["events"]][:3] == ["RunStart", "MessageEnd", "TurnStart"]

    base = f"/v1/sessions/{sid}/runs"
    assert (await api.client.get(f"{base}/r1/trace/events", headers=BOB)).status_code == 404
    assert (await api.client.get(f"{base}/nope/trace/events", headers=ALICE)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace", headers=BOB)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace", headers=T2)).status_code == 404
    assert (await api.client.get(f"{base}/nope/trace", headers=ALICE)).status_code == 404
    assert (await api.client.get(f"{base}/r1/trace/spans/0000", headers=ALICE)).status_code == 404


async def test_a_run_is_recorded_when_it_starts_and_again_when_it_ends(api: Api) -> None:
    sid = await api.session()
    reader = JournalReader(api.factory)

    async def row() -> RunTraceRow | None:
        async with api.factory() as db:
            return await db.get(RunTraceRow, (sid, "r1"))

    await api.journal(sid, "r1", (1_000, RunStart()))
    await record_run(api.factory, reader, sid, "r1")
    started = await row()
    assert started is not None and started.status == "running" and started.ended_ms is None

    await api.journal(sid, "r1", (1_500, TurnEnd("stop")), (1_500, RunEnd(messages=[])))
    await record_run(api.factory, reader, sid, "r1")
    ended = await row()
    assert ended is not None and ended.status == "ok" and ended.duration_ms == 500

    await record_run(api.factory, reader, "no-such-session", "r1")  # never raises

    # A row an older builder wrote is rebuilt the next time it is read.
    async with api.factory() as db, db.begin():
        stale = await db.get(RunTraceRow, (sid, "r1"))
        assert stale is not None
        stale.rollup, stale.status, stale.duration_ms = {"v": 1}, "error", 1
    listed = (await api.client.get(f"/v1/sessions/{sid}/traces", headers=ALICE)).json()["items"]
    assert [(i["status"], i["duration_ms"]) for i in listed] == [("ok", 500)]


async def test_finished_runs_are_checked_and_people_can_say_how_it_went(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "fine", "all good", start=1_000)
    await api.run(sid, "broke", "tool fails", start=2_000, tool_ok=False)

    failing = await list_traces(api, ALICE, failing_checks="true")
    assert [i["run_id"] for i in failing["items"]] == ["broke"] and failing["items"][0]["checks_failed"] == 1

    tree = (await api.client.get(f"/v1/sessions/{sid}/runs/broke/trace", headers=ALICE)).json()
    checks = {s["name"]: s for s in tree["scores"] if s["source"] == "check"}
    assert checks["finished"]["label"] == "pass" and checks["tool_errors"]["label"] == "fail"
    tool = next(s for s in tree["spans"] if s["kind"] == "tool")
    assert checks["tool_errors"]["span_id"] == tool["id"] and checks["tool_errors"]["evaluator"] == "checks@2"

    base = f"/v1/sessions/{sid}/runs/broke/feedback"
    r = await api.client.put(base, json={"good": False, "comment": "should have checked the file exists"}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "bad" and r.json()["principal_id"] == "alice"
    assert (await api.client.put(base, json={"good": False}, headers=ALICE)).status_code == 200  # replaces
    span_vote = await api.client.put(base, json={"good": True, "span_id": tool["id"]}, headers=ALICE)
    assert span_vote.status_code == 200
    assert (await api.client.put(base, json={"good": True, "span_id": "0" * 16}, headers=ALICE)).status_code == 404

    row = (await list_traces(api, ALICE, feedback="bad"))["items"]
    assert [(i["run_id"], i["feedback_bad"], i["feedback_good"]) for i in row] == [("broke", 1, 1)]
    assert (await list_traces(api, ALICE, feedback="good"))["items"][0]["run_id"] == "broke"

    assert (await api.client.put(base, json={"good": True}, headers=BOB)).status_code == 404  # not Bob's session
    assert (await api.client.delete(base, headers=ALICE)).status_code == 204
    assert (await api.client.delete(base, headers=ALICE)).status_code == 404
    assert (await list_traces(api, ALICE, feedback="bad"))["items"] == []


async def test_versions_are_compared_on_their_finished_runs(api: Api) -> None:
    sid = await api.session()
    await api.run(sid, "a", "one", start=1_000)
    await api.run(sid, "b", "two", start=2_000, tool_ok=False)
    await api.run(sid, "c", "three", start=3_000, ended=False)  # still going: not counted
    await list_traces(api, ALICE)  # fills in the rollups
    await api.client.put(f"/v1/sessions/{sid}/runs/a/feedback", json={"good": True}, headers=ALICE)

    r = await api.client.get("/v1/traces/versions", params={"composition_id": "githi-v1"}, headers=ALICE)
    assert r.status_code == 200, r.text
    (v,) = r.json()["versions"]
    assert v["composition_digest"] == "sha256:" + "a" * 64
    assert (v["runs"], v["answered"], v["failed"], v["runs_failing_checks"]) == (2, 2, 0, 1)
    assert (v["feedback_good"], v["feedback_bad"]) == (1, 0) and v["avg_cost"] == 0.03

    other = await api.client.get("/v1/traces/versions", params={"composition_id": "githi-v1"}, headers=BOB)
    assert other.json()["versions"] == []
