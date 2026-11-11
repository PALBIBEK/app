"""The CDP supervisor's reconnect budget (``supervisor.hands.browser.cdp_supervisor``): a
supervisor that attached once never retries a dead browser forever. Hermes's
``tests/tools/test_browser_supervisor_reconnect.py``; no browser, the socket is stubbed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from supervisor.hands.browser import cdp_supervisor as cs


class _ClosingWebSocket:
    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_post_attach_reconnects_stop_at_the_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """After the first attach, a dead endpoint gets MAX_POST_ATTACH_RECONNECT_FAILURES dials
    and one last warning, then the supervisor gives up: never an unbounded loop."""
    supervisor = cs.CDPSupervisor(task_id="bounded-reconnect", cdp_url="ws://127.0.0.1:9222")
    budget = cs.MAX_POST_ATTACH_RECONNECT_FAILURES
    dials = 0

    async def connect(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal dials
        dials += 1
        if dials == 1:
            return _ClosingWebSocket()
        if dials <= budget + 1:
            raise ConnectionError("CDP endpoint is gone")
        await asyncio.Event().wait()  # would hang forever: the loop must never get here

    async def _noop(*_a: Any, **_k: Any) -> None:
        pass

    real_sleep = asyncio.sleep

    async def fast_sleep(_delay: float) -> None:
        await real_sleep(0)  # yield, so wait_for's deadline can fire on an unbounded loop

    monkeypatch.setattr(cs._socket, "connect", connect)
    monkeypatch.setattr(supervisor, "_attach_pages", _noop)
    monkeypatch.setattr(supervisor, "_read_loop", _noop)
    monkeypatch.setattr(cs.asyncio, "sleep", fast_sleep)

    with caplog.at_level(logging.WARNING, logger="githi.browser.supervisor"):
        await asyncio.wait_for(supervisor._run(), timeout=2.0)

    assert dials == budget + 1  # the attach, then the budgeted reconnects
    final = [r.getMessage() for r in caplog.records if "stopped after" in r.getMessage()]
    assert len(final) == 1 and f"{budget} failed reconnect" in final[0]
    assert supervisor.snapshot().active is False and supervisor.gave_up is True


@pytest.mark.asyncio
async def test_a_first_connect_failure_stays_fatal_for_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The budget is for after the first attach: a failed first dial is ``start()``'s."""
    supervisor = cs.CDPSupervisor(
        task_id="initial-failure", cdp_url="ws://127.0.0.1:9222/devtools/browser/x?token=SECRET"
    )

    async def connect(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionError(
            "CDP endpoint is unavailable at ws://127.0.0.1:9222/devtools/browser/x?token=SECRET"
        )

    monkeypatch.setattr(cs._socket, "connect", connect)

    with pytest.raises(RuntimeError, match="failed to start") as failed:
        await supervisor.start(timeout=2.0)

    assert "SECRET" not in str(failed.value), "the endpoint's token reached the error"
    assert supervisor.snapshot().active is False


@pytest.mark.asyncio
async def test_an_invalid_dialog_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="dialog_policy"):
        cs.CDPSupervisor(task_id="t", cdp_url="ws://x", dialog_policy="sometimes")


def test_a_urls_credentials_are_masked() -> None:
    masked = cs.redact_url(
        "dial wss://user:pass@connect.example.com/?apiKey=abc123&session=s1 failed"
    )

    assert "pass" not in masked and "abc123" not in masked and "s1" not in masked
    assert "connect.example.com" in masked


def _supervisor_with(frames: dict[str, Any]) -> Any:
    supervisor = cs.CDPSupervisor(task_id="t", cdp_url="ws://x")
    supervisor._frames = frames
    supervisor._active = True
    return supervisor


@pytest.mark.asyncio
async def test_a_call_into_a_frame_it_does_not_know_or_of_the_pages_process_says_so() -> None:
    frames = cs._frames.FrameInfo
    supervisor = _supervisor_with(
        {
            "top": frames("top", "http://a/", "http://a", None, False, "s-top"),
            "same": frames("same", "about:srcdoc", "http://a", "top", False, "s-top"),
        }
    )

    unknown = await supervisor.call("Runtime.evaluate", {"expression": "1"}, frame_id="nope")
    same = await supervisor.call("Runtime.evaluate", {"expression": "1"}, frame_id="same")

    assert unknown["ok"] is False and "not found" in unknown["error"]
    assert same["ok"] is False and "not an out-of-process iframe" in same["error"]


def test_the_frame_tree_is_capped() -> None:
    frames = cs._frames.FrameInfo
    many = {"top": frames("top", "http://a/", "http://a", None, False, "s")}
    many.update(
        {
            f"f{i}": frames(f"f{i}", f"http://ad{i}/", f"http://ad{i}", "top", True, f"c{i}")
            for i in range(40)
        }
    )

    tree = _supervisor_with(many).snapshot().frame_tree

    assert tree["top"]["frame_id"] == "top"
    assert len(tree["children"]) == cs._frames.FRAME_TREE_MAX_ENTRIES and tree["truncated"] is True
