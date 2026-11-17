"""The CDP supervisor: one live DevTools connection that sees every dialog and every frame.

Hermes's ``tools/browser_supervisor.py``, run by the sandbox's browser daemon on its own
loop (Hermes runs one per task, in a thread of the agent's process; here the daemon is the
one long-lived process beside the browser). One WebSocket to the browser
(``cdp_socket``), ``Page`` / ``Runtime`` / ``Target`` / ``Fetch`` events from every
attached session: each tab, and the out-of-process iframes and workers auto-attached under
it. Pending dialogs and the frame tree are kept for the daemon to answer calls with:

* ``browser_dialog`` answers a dialog (``respond_to_dialog``);
* ``browser_cdp`` sends a raw command, into an out-of-process iframe by its ``frame_id``
  over the child session (``call``), or into a tab by ``target_id``;
* the runner reports the dialogs that opened during a call.

Two gaps it closes, as Hermes's does: a native dialog blocks its page's JavaScript, so
without one calls hang or fail opaquely; and an iframe of another site is out of reach of
the top page's ``Runtime.evaluate``, so without a session attached to it nothing can act in
it. Dialogs are caught by the dialog bridge (``cdp_dialogs``) too, which works on a hosted
browser that dismisses native dialogs itself.

Unlike Hermes's, which attaches to the first tab and is re-pointed by ``focus_page``, this
one attaches to every tab, current and new: the agent's code opens and switches tabs, and a
dialog in any of them must be seen. ``evaluate_runtime`` acts in the first tab attached.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar


def _sibling(name: str) -> ModuleType:
    """A module of the runtime beside this file, loaded by path (the runtime is a folder of
    scripts, not a package), once."""
    key = f"githi_browser_{name}"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = sys.modules[key] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_dialogs = _sibling("cdp_dialogs")
_frames = _sibling("cdp_frames")
_socket = _sibling("cdp_socket")
DialogSupervisionMixin = _dialogs.DialogSupervisionMixin
FrameTrackingMixin = _frames.FrameTrackingMixin
DialogRecord = _dialogs.DialogRecord
PendingDialog = _dialogs.PendingDialog
DEFAULT_DIALOG_POLICY = _dialogs.DEFAULT_DIALOG_POLICY
DEFAULT_DIALOG_TIMEOUT_S = _dialogs.DEFAULT_DIALOG_TIMEOUT_S
RECENT_DIALOGS_MAX = _dialogs.RECENT_DIALOGS_MAX
VALID_POLICIES = _dialogs.VALID_POLICIES

logger = logging.getLogger("githi.browser.supervisor")

MAX_POST_ATTACH_RECONNECT_FAILURES = 5
"""A hosted browser drops its CDP socket for a moment while a short-lived client
reconnects; a browser of the sandbox's own is gone for good once its process exits. Room
for the first, without retrying forever for the second."""

_SECRET_QUERY = re.compile(r"([?&][^=&#\s]+=)[^&#\s]+")
_USERINFO = re.compile(r"(\w+://)[^/@\s]+@")


def redact_url(value: object) -> str:
    """``value``'s text with a URL's credentials masked: query values (a hosted browser's
    connect URL carries its token as one) and ``user:pass@``. Every error that may hold the
    endpoint goes through here before it is logged or answered."""
    try:
        return _USERINFO.sub(r"\1***@", _SECRET_QUERY.sub(r"\1***", str(value)))
    except Exception:  # noqa: BLE001 — err toward masking
        return "<error redacted>"


def _fail(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _err(exc: BaseException) -> dict[str, Any]:
    return _fail(f"{type(exc).__name__}: {redact_url(exc)}")


@dataclass(frozen=True)
class SupervisorSnapshot:
    """What the supervisor knows now, read-only."""

    pending_dialogs: tuple[Any, ...]
    recent_dialogs: tuple[Any, ...]
    frame_tree: dict[str, Any]
    active: bool
    """False while detached or stopped."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pending_dialogs": [d.to_dict() for d in self.pending_dialogs],
            "frame_tree": self.frame_tree,
        }
        if self.recent_dialogs:
            out["recent_dialogs"] = [d.to_dict() for d in self.recent_dialogs]
        return out


class CDPSupervisor(DialogSupervisionMixin, FrameTrackingMixin):  # type: ignore[misc,valid-type]
    """One supervisor per browser. ``start()`` connects, attaches to every tab, enables the
    domains and auto-attach, and installs the dialog bridge; its reader then runs until
    ``stop()``, reconnecting when the socket drops."""

    def __init__(
        self,
        task_id: str,
        cdp_url: str,
        *,
        dialog_policy: str = DEFAULT_DIALOG_POLICY,
        dialog_timeout_s: float = DEFAULT_DIALOG_TIMEOUT_S,
        on_dialog: Callable[[str, Any], None] | None = None,
    ) -> None:
        if dialog_policy not in VALID_POLICIES:
            raise ValueError(
                f"Invalid dialog_policy {dialog_policy!r}; must be one of {sorted(VALID_POLICIES)}"
            )
        self.task_id = task_id
        self.cdp_url = cdp_url
        self.dialog_policy = dialog_policy
        self.dialog_timeout_s = float(dialog_timeout_s)
        self.prompt_text: str | None = None
        self._on_dialog_change = on_dialog
        self._pending_dialogs: dict[str, Any] = {}
        self._recent_dialogs: list[Any] = []
        self._frames: dict[str, Any] = {}
        self._active = False
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._start_error: BaseException | None = None
        self._stop_requested = False
        self.gave_up = False
        """Set once reconnecting failed ``MAX_POST_ATTACH_RECONNECT_FAILURES`` times in a row."""
        self._next_call_id = 1
        self._pending_calls: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._ws: Any = None
        self._page_session_id: str | None = None
        self._page_sessions: dict[str, str] = {}
        """Tab (target id) -> its attached session."""
        self._attaching: dict[str, asyncio.Future[str]] = {}
        self._dialog_watchdogs: dict[str, asyncio.TimerHandle] = {}
        self._dialog_seq = 0

    # Public API ----------------------------------------------------------------

    async def start(self, timeout: float = 15.0) -> None:
        """Connect and attach, and return once attached; raise (redacted) why not."""
        if self._task is not None and not self._task.done():
            return
        self._ready.clear()
        self._start_error, self._stop_requested = None, False
        self._task = asyncio.ensure_future(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except TimeoutError:
            await self.stop()
            raise TimeoutError(
                f"CDP supervisor did not attach within {timeout}s (cdp_url={redact_url(self.cdp_url)[:80]}...)"
            ) from None
        if (err := self._start_error) is not None:
            await self.stop()
            raise RuntimeError(f"CDP supervisor failed to start: {redact_url(err)}") from None

    async def stop(self) -> None:
        self._stop_requested = True
        await self._close_ws()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def snapshot(self) -> SupervisorSnapshot:
        return SupervisorSnapshot(
            pending_dialogs=tuple(self._pending_dialogs.values()),
            recent_dialogs=tuple(self._recent_dialogs[-RECENT_DIALOGS_MAX:]),
            frame_tree=self._build_frame_tree(),
            active=self._active,
        )

    async def respond_to_dialog(
        self,
        action: str,
        *,
        prompt_text: str | None = None,
        dialog_id: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Accept or dismiss a pending dialog: ``{"ok": True, "dialog"}``, or ``{"ok": False,
        "error"}`` for what the caller can fix."""
        if action not in {"accept", "dismiss"}:
            return _fail(f"action must be 'accept' or 'dismiss', got {action!r}")
        if not self._active:
            return _fail("supervisor is not active")
        pending = list(self._pending_dialogs.values())
        if not pending:
            return _fail("no dialog is currently open")
        if dialog_id:
            dialog = self._pending_dialogs.get(dialog_id)
            if dialog is None:
                return _fail(
                    f"dialog_id {dialog_id!r} not found (known: {sorted(self._pending_dialogs)})"
                )
        elif len(pending) > 1:
            return _fail(
                f"{len(pending)} pending dialogs; specify dialog_id. Candidates: {[d.id for d in pending]}"
            )
        else:
            dialog = pending[0]
        try:
            await asyncio.wait_for(
                self._handle_dialog_cdp(
                    dialog, accept=(action == "accept"), prompt_text=prompt_text or ""
                ),
                timeout,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
        return {"ok": True, "dialog": dialog.to_dict()}

    async def evaluate_runtime(
        self,
        expression: str,
        *,
        return_by_value: bool = True,
        await_promise: bool = True,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Evaluate ``expression`` in the focused tab over the live socket: ``{"ok": True,
        "result", "result_type"}`` or ``{"ok": False, "error"}``. By value, as DevTools'
        console does; what cannot be serialized comes back as its description."""
        if not self._active:
            return _fail("supervisor is not active")
        session_id = self._page_session_id
        if not session_id:
            return _fail("supervisor has no attached page session")

        async def run(by_value: bool) -> dict[str, Any]:
            # userGesture: clipboard and fullscreen need user activation.
            params = {
                "expression": expression,
                "returnByValue": by_value,
                "awaitPromise": await_promise,
                "userGesture": True,
            }
            return await self._cdp(
                "Runtime.evaluate", params, session_id=session_id, timeout=timeout
            )

        try:
            response = await run(return_by_value)
        except Exception as exc:  # noqa: BLE001
            # Serializing live DOM nodes, NodeLists or the Window by value can pass CDP's
            # recursion guard ("Object reference chain is too long"): once more, by reference,
            # for Chrome's description of it.
            if not (return_by_value and "reference chain is too long" in str(exc).lower()):
                return _err(exc)
            try:
                response = await run(False)
            except Exception as exc2:  # noqa: BLE001
                return _err(exc2)
        result_payload = response.get("result", {}) if isinstance(response, dict) else {}
        if exception_details := result_payload.get("exceptionDetails"):
            exc_text = exception_details.get("text") or "JavaScript exception"
            description = (exception_details.get("exception") or {}).get("description")
            return _fail(f"{exc_text}: {description}" if description else exc_text)
        result_obj = result_payload.get("result", {})
        result_type = result_obj.get("type", "undefined")
        if "value" in result_obj:
            value = result_obj["value"]
        elif result_type == "undefined":
            value = None
        else:  # functions, DOM nodes, Infinity: the browser's description of it
            value = result_obj.get("description") or result_obj.get("unserializableValue")
        return {"ok": True, "result": value, "result_type": result_type}

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        target_id: str | None = None,
        frame_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """One raw CDP command over the live socket (``browser_cdp``): at browser level, in a
        tab (``target_id``), or in an out-of-process iframe (``frame_id``, through its child
        session). ``{"ok": True, "result", ...}`` or ``{"ok": False, "error"}``."""
        if not self._active:
            return _fail("supervisor is not active")
        session_id: str | None = None
        routed: dict[str, Any] = {}
        if frame_id:
            frame = self._frames.get(frame_id)
            if frame is None:
                return _fail(f"frame_id {frame_id!r} not found: frames() lists the page's frames")
            if not frame.cdp_session_id or not frame.is_oopif:
                return _fail(
                    f"frame_id {frame_id!r} is not an out-of-process iframe (no session of its own): "
                    "reach a same-origin iframe from its page, through contentDocument in Runtime.evaluate"
                )
            session_id = frame.cdp_session_id
            routed = {"frame_id": frame_id}
        elif target_id:
            try:
                session_id = self._page_sessions.get(target_id) or await self._attach_page(
                    target_id
                )
            except Exception as exc:  # noqa: BLE001
                return _fail(f"Target.attachToTarget failed: {redact_url(exc)}")
            routed = {"target_id": target_id}
        try:
            reply = await self._cdp(method, params or {}, session_id=session_id, timeout=timeout)
        except TimeoutError:
            return _fail(f"CDP call timed out after {timeout}s")
        except Exception as exc:  # noqa: BLE001
            return _fail(redact_url(exc))
        return {"ok": True, "method": method, **routed, "result": reply.get("result", {})}

    # The loop ------------------------------------------------------------------

    def _fail_start(self, e: BaseException) -> bool:
        """Hand ``e`` to ``start()`` if it is still waiting; True if it took it."""
        if self._ready.is_set():
            return False
        self._start_error = e
        self._ready.set()
        return True

    async def _close_ws(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()

    def _reconnect_budget_spent(self, failures: int, e: BaseException) -> bool:
        """True once ``failures`` reconnects in a row failed: log ONE last line and give up,
        so a browser that is gone (its process exited) leaves no reconnecting task behind."""
        if failures < MAX_POST_ATTACH_RECONNECT_FAILURES:
            return False
        logger.warning(
            "CDP supervisor %s: stopped after %s failed reconnect attempts: %s",
            self.task_id,
            failures,
            redact_url(e),
        )
        self.gave_up = True
        return True

    async def _run(self) -> None:
        """Connect, attach and read, again whenever the socket drops: a hosted browser tears
        it down when a short-lived client (a call's own connection) disconnects. A failure
        before the first attach is ``start()``'s."""
        reconnect_failures, last_success_at, backoff = 0, 0.0, 0.5
        while not self._stop_requested:
            try:
                self._ws = await _socket.connect(
                    self.cdp_url, max_size=50 * 1024 * 1024, timeout=10.0
                )
            except Exception as e:  # noqa: BLE001
                if self._fail_start(e):
                    return
                reconnect_failures += 1
                if self._reconnect_budget_spent(reconnect_failures, e):
                    return
                logger.warning(
                    "CDP supervisor %s: connect failed (attempt %s/%s): %s",
                    self.task_id,
                    reconnect_failures,
                    MAX_POST_ATTACH_RECONNECT_FAILURES,
                    redact_url(e),
                )
                await asyncio.sleep(min(backoff, 10.0))
                backoff = min(backoff * 2, 10.0)
                continue
            reader_task = asyncio.ensure_future(self._read_loop())
            try:
                # Sessions are per connection; dialogs and frames are kept and reconcile as
                # events come (at worst a stale dialog is refused: "no dialog is showing").
                self._page_session_id = None
                self._page_sessions.clear()
                self._attaching.clear()
                await self._attach_pages()
                self._active = True
                last_success_at = time.time()
                reconnect_failures = 0
                backoff = 0.5
                self._ready.set()
                await reader_task
            except BaseException as e:
                if self._fail_start(e):
                    return
                if isinstance(e, asyncio.CancelledError):
                    raise
                reconnect_failures += 1
                if self._reconnect_budget_spent(reconnect_failures, e):
                    return
                logger.warning(
                    "CDP supervisor %s: session dropped after %.1fs (attempt %s/%s): %s",
                    self.task_id,
                    time.time() - last_success_at,
                    reconnect_failures,
                    MAX_POST_ATTACH_RECONNECT_FAILURES,
                    redact_url(e),
                )
            finally:
                self._active = False
                if not reader_task.done():
                    reader_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await reader_task
                for handle in self._dialog_watchdogs.values():
                    handle.cancel()
                self._dialog_watchdogs.clear()
                await self._close_ws()
                # The replies to calls in flight died with the socket: fail them now rather
                # than each sitting out its timeout.
                for fut in self._pending_calls.values():
                    if not fut.done():
                        fut.set_exception(ConnectionError("CDP connection closed before reply"))
                self._pending_calls.clear()
            if self._stop_requested:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10.0)

    async def _attach_pages(self) -> None:
        """Attach to every tab (making one if there is none), then to every tab made later.
        Discovery is turned on after: it announces the tabs there already are, and those are
        attached by then (two sessions on one tab would catch each dialog twice)."""
        targets = (await self._cdp("Target.getTargets")).get("result", {}).get("targetInfos", [])
        pages = [t for t in targets if t.get("type") == "page"]
        if not pages:
            pages = [(await self._cdp("Target.createTarget", {"url": "about:blank"}))["result"]]
        for page in pages:
            sid = await self._attach_page(page["targetId"])
            if self._page_session_id is None:
                self._page_session_id = sid
        await self._cdp("Target.setDiscoverTargets", {"discover": True})

    async def _attach_page(self, target_id: str) -> str:
        """The tab's session, attaching once however many ask at the same time."""
        if (sid := self._page_sessions.get(target_id)) is not None:
            return sid
        if (pending := self._attaching.get(target_id)) is not None:
            return await asyncio.shield(pending)
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._attaching[target_id] = future
        try:
            attach = await self._cdp(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            sid = str(attach["result"]["sessionId"])
            self._page_sessions[target_id] = sid
            await self._enable_page_domains(sid, timeout=10.0)
            await self._install_dialog_bridge(sid)
            await self._seed_frames(sid, timeout=10.0)
            future.set_result(sid)
            return sid
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # retrieved: nobody else may be waiting
            raise
        finally:
            self._attaching.pop(target_id, None)

    async def _on_target_created(
        self, params: dict[str, Any], session_id: str | None = None
    ) -> None:
        info = params.get("targetInfo") or {}
        target_id = info.get("targetId")
        if info.get("type") == "page" and target_id and target_id not in self._page_sessions:
            asyncio.ensure_future(
                self._attach_new_page(target_id)
            )  # off the reader: it resolves the replies

    async def _attach_new_page(self, target_id: str) -> None:
        try:
            await self._attach_page(target_id)
        except Exception as e:  # noqa: BLE001 — closed before we attached
            logger.debug("attaching to tab %s failed: %s", target_id[:16], e)

    def _on_target_destroyed(self, params: dict[str, Any], session_id: str | None = None) -> None:
        sid = self._page_sessions.pop(str(params.get("targetId") or ""), None)
        if sid is not None and sid == self._page_session_id:
            self._page_session_id = next(iter(self._page_sessions.values()), None)

    async def _cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = 10.0,
    ) -> dict[str, Any]:
        """Send a CDP command and wait for its reply."""
        if self._ws is None:
            raise RuntimeError("supervisor WebSocket is not connected")
        call_id, self._next_call_id = self._next_call_id, self._next_call_id + 1
        payload: dict[str, Any] = {"id": call_id, "method": method}
        payload.update({k: v for k, v in (("params", params), ("sessionId", session_id)) if v})
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_calls[call_id] = fut
        await self._ws.send(json.dumps(payload))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending_calls.pop(call_id, None)

    async def _read_loop(self) -> None:
        """Dispatch every frame that comes: replies to their futures, events to their handlers."""
        assert self._ws is not None
        async for raw in self._ws:
            if self._stop_requested:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                logger.debug("CDP supervisor: non-JSON frame dropped")
                continue
            if "id" in msg:
                fut = self._pending_calls.pop(msg["id"], None)
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    fut.set_exception(RuntimeError(f"CDP error on id={msg['id']}: {msg['error']}"))
                else:
                    fut.set_result(msg)
            elif handler := self._EVENT_HANDLERS.get(msg.get("method")):
                try:
                    result = handler(self, msg.get("params", {}), msg.get("sessionId"))
                    if result is not None:
                        await result
                except Exception as e:  # noqa: BLE001 — one bad event must not end the reader
                    logger.debug("CDP supervisor: handling %s failed: %s", msg.get("method"), e)

    # CDP event -> handler(self, params, session_id). Async handlers return an awaitable the
    # reader awaits; sync handlers return None.
    _EVENT_HANDLERS: ClassVar[dict[str, Callable[..., Any]]] = {
        **DialogSupervisionMixin.EVENT_HANDLERS,
        **FrameTrackingMixin.EVENT_HANDLERS,
        "Target.targetCreated": _on_target_created,
        "Target.targetDestroyed": _on_target_destroyed,
    }


__all__ = ["CDPSupervisor", "SupervisorSnapshot", "redact_url"]
