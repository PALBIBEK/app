"""Dialog capture and response, the half of the CDP supervisor (``cdp_supervisor.py``) that
keeps a page from hanging on ``alert`` / ``confirm`` / ``prompt``.

Hermes's ``tools/browser_supervisor_dialogs.py``, on the daemon's loop. Two capture paths
feed one ``PendingDialog`` queue: native ``Page.javascriptDialogOpening`` events (answered
with ``Page.handleJavaScriptDialog``), and the injected *dialog bridge*: a page script
rewriting alert/confirm/prompt into a synchronous XHR to a magic host, intercepted with the
CDP ``Fetch`` domain and answered with ``Fetch.fulfillRequest``. The bridge works on a
hosted browser whose CDP proxy auto-dismisses native dialogs (Browserbase's does, within
about 10 ms), because the native dialog never fires. ``DialogSupervisionMixin`` relies on
the state ``CDPSupervisor.__init__`` sets.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("githi.browser.supervisor")


def _trim_ring(events: list[Any], keep: int) -> list[Any]:
    """Cap a ring buffer at ``keep`` entries once it overflows to 2x (slack reduces churn)."""
    return events[-keep:] if len(events) > keep * 2 else events


DIALOG_POLICY_MUST_RESPOND = "must_respond"
DIALOG_POLICY_AUTO_DISMISS = "auto_dismiss"
DIALOG_POLICY_AUTO_ACCEPT = "auto_accept"
VALID_POLICIES = frozenset(
    {DIALOG_POLICY_MUST_RESPOND, DIALOG_POLICY_AUTO_DISMISS, DIALOG_POLICY_AUTO_ACCEPT}
)
DEFAULT_DIALOG_POLICY = DIALOG_POLICY_MUST_RESPOND
DEFAULT_DIALOG_TIMEOUT_S = 300.0

RECENT_DIALOGS_MAX = 20
"""Closed dialogs kept, so an agent on a backend that dismisses them itself can still see one fired."""

DIALOG_BRIDGE_HOST = "githi-dialog-bridge.invalid"
"""The host the bridge's XHRs go to: intercepted with Fetch before any network resolution,
so it never has to exist (ASCII and URL-safe: Fetch patterns match on it)."""
DIALOG_BRIDGE_URL_PATTERN = f"http://{DIALOG_BRIDGE_HOST}/*"

# Injected into every frame via Page.addScriptToEvaluateOnNewDocument. A synchronous GET
# with query parameters, so the Fetch interceptor never parses a body; an unreachable bridge
# answers null, so the page still sees *some* behaviour. onbeforeunload stays native (it
# cannot be prompted synchronously without racing the navigation); the native path records it.
DIALOG_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__githiDialogBridgeInstalled) return;
  window.__githiDialogBridgeInstalled = true;
  const ENDPOINT = "http://githi-dialog-bridge.invalid/";
  function ask(kind, message, defaultPrompt) {
    try {
      const xhr = new XMLHttpRequest();
      const params = new URLSearchParams({
        kind: String(kind || ""),
        message: String(message == null ? "" : message),
        default_prompt: String(defaultPrompt == null ? "" : defaultPrompt),
      });
      xhr.open("GET", ENDPOINT + "?" + params.toString(), false);  // sync
      xhr.send(null);
      if (xhr.status !== 200) return null;
      let parsed;
      try { parsed = JSON.parse(xhr.responseText || ""); } catch (e) { return null; }
      if (kind === "alert") return undefined;
      if (kind === "confirm") return Boolean(parsed && parsed.accept);
      if (kind === "prompt") {
        if (!parsed || !parsed.accept) return null;
        return parsed.prompt_text == null ? "" : String(parsed.prompt_text);
      }
      return null;
    } catch (e) {
      return null;
    }
  }
  window.alert   = function(message) { ask("alert",   message, ""); };
  window.confirm = function(message) {
    const r = ask("confirm", message, "");
    return r === null ? false : Boolean(r);
  };
  window.prompt  = function(message, def) {
    const r = ask("prompt", message, def == null ? "" : def);
    return r === null ? null : String(r);
  };
})();
"""

TEXT_CAP = 300
"""Characters of a dialog's message (and default prompt) shown."""


@dataclass
class PendingDialog:
    """A JS dialog currently open on some frame's session."""

    id: str
    type: str  # "alert" | "confirm" | "prompt" | "beforeunload"
    message: str
    default_prompt: str
    opened_at: float
    cdp_session_id: str  # which attached CDP session the dialog fired in
    frame_id: str | None = None
    bridge_request_id: str | None = None
    """Set when it came through the bridge's XHR: answered by Fetch.fulfillRequest, not
    Page.handleJavaScriptDialog."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "message": self.message[:TEXT_CAP],
            "default_prompt": self.default_prompt[:TEXT_CAP],
            "opened_at": self.opened_at,
        }
        if self.frame_id:
            out["frame_id"] = self.frame_id
        return out


@dataclass
class DialogRecord:
    """A dialog that was opened and then handled (kept briefly in ``recent_dialogs``)."""

    id: str
    type: str
    message: str
    opened_at: float
    closed_at: float
    closed_by: str  # "agent" | "auto_policy" | "remote" | "watchdog"
    frame_id: str | None = None
    accepted: bool | None = None
    """How it was answered; None when the page or the browser closed it."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "message": self.message[:TEXT_CAP],
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "closed_by": self.closed_by,
        }
        if self.accepted is not None:
            out["answer"] = "accept" if self.accepted else "dismiss"
        if self.frame_id:
            out["frame_id"] = self.frame_id
        return out


class DialogSupervisionMixin:
    """Dialog event handling for ``CDPSupervisor`` (every method runs on its loop)."""

    # Set by CDPSupervisor.__init__.
    task_id: str
    dialog_policy: str
    dialog_timeout_s: float
    prompt_text: str | None
    """What an auto-accepted prompt answers (None: its default)."""
    _pending_dialogs: dict[str, PendingDialog]
    _recent_dialogs: list[DialogRecord]
    _dialog_watchdogs: dict[str, asyncio.TimerHandle]
    _dialog_seq: int
    _page_session_id: str | None
    _on_dialog_change: Callable[[str, PendingDialog | DialogRecord], None] | None

    async def _cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = 10.0,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _cdp_quiet(
        self,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str | None,
        timeout: float,
        what: str,
    ) -> None:
        """Best-effort CDP call: failures are logged at debug and swallowed."""
        try:
            await self._cdp(method, params, session_id=session_id, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            logger.debug("%s failed (%s): %s", method, what, e)

    async def _install_dialog_bridge(self, session_id: str) -> None:
        """Install the dialog bridge's init script and Fetch interceptor on a session.
        Idempotent at the CDP level (Chromium de-dupes identical add-script calls; Fetch.enable
        replaces earlier patterns); the final Runtime.evaluate injects it into the document
        already loaded, so pages open before a reconnect get it too."""
        sid = (session_id or "")[:16]
        steps = (
            (
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": DIALOG_BRIDGE_SCRIPT, "runImmediately": True},
                5.0,
                f"dialog bridge sid={sid}",
            ),
            (
                "Fetch.enable",
                {
                    "patterns": [
                        {"urlPattern": DIALOG_BRIDGE_URL_PATTERN, "requestStage": "Request"}
                    ],
                    "handleAuthRequests": False,
                },
                5.0,
                f"dialog bridge sid={sid}",
            ),
            (
                "Runtime.evaluate",
                {"expression": DIALOG_BRIDGE_SCRIPT, "returnByValue": True},
                3.0,
                f"dialog bridge inject sid={sid}",
            ),
        )
        for method, params, timeout, what in steps:
            await self._cdp_quiet(method, params, session_id=session_id, timeout=timeout, what=what)

    # Capture -------------------------------------------------------------------

    async def _on_dialog_opening(self, params: dict[str, Any], session_id: str | None) -> None:
        self._admit_dialog(
            type=str(params.get("type") or ""),
            message=str(params.get("message") or ""),
            default_prompt=str(params.get("defaultPrompt") or ""),
            session_id=session_id,
            frame_id=params.get("frameId"),
        )

    async def _on_fetch_paused(self, params: dict[str, Any], session_id: str | None) -> None:
        """A bridge XHR caught mid-flight, made a pending dialog. The page's JS thread blocks
        on the XHR until we fulfil it (the agent, a policy or the watchdog); requests for
        other hosts are passed on unchanged."""
        url = str(params.get("request", {}).get("url") or "")
        request_id = params.get("requestId")
        if not request_id:
            return
        if DIALOG_BRIDGE_HOST not in url:
            await self._cdp_quiet(
                "Fetch.continueRequest",
                {"requestId": request_id},
                session_id=session_id,
                timeout=3.0,
                what="passthrough",
            )
            return
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self._admit_dialog(
            type=q.get("kind") or "alert",
            message=q.get("message", ""),
            default_prompt=q.get("default_prompt", ""),
            session_id=session_id,
            frame_id=params.get("frameId"),
            bridge_request_id=str(request_id),
        )

    def _admit_dialog(
        self,
        *,
        type: str,  # CDP's own name
        message: str,
        default_prompt: str,
        session_id: str | None,
        frame_id: str | None,
        bridge_request_id: str | None = None,
    ) -> None:
        """Make the dialog and apply the policy: answer it, or queue it and arm the watchdog.
        An auto policy archives FIRST (as ``auto_policy``), so the ``closed`` event our own
        answer causes is not archived again as ``remote``."""
        self._dialog_seq += 1
        dialog = PendingDialog(
            id=f"d-{self._dialog_seq}",
            type=type,
            message=message,
            default_prompt=default_prompt,
            opened_at=time.time(),
            cdp_session_id=session_id or self._page_session_id or "",
            frame_id=frame_id,
            bridge_request_id=bridge_request_id,
        )
        answer_text = default_prompt if self.prompt_text is None else self.prompt_text
        auto = {
            DIALOG_POLICY_AUTO_DISMISS: (False, ""),
            DIALOG_POLICY_AUTO_ACCEPT: (True, answer_text),
        }.get(self.dialog_policy)
        if auto is not None:
            self._archive_dialog(dialog, "auto_policy", accepted=auto[0])
            asyncio.ensure_future(self._respond_quiet(dialog, accept=auto[0], prompt_text=auto[1]))
            return
        self._pending_dialogs[dialog.id] = dialog
        self._changed("opened", dialog)
        self._dialog_watchdogs[dialog.id] = asyncio.get_running_loop().call_later(
            self.dialog_timeout_s,
            lambda: asyncio.ensure_future(self._dialog_timeout_expired(dialog.id)),
        )

    # Responding ----------------------------------------------------------------

    async def _respond(
        self, dialog: PendingDialog, *, accept: bool, prompt_text: str | None
    ) -> None:
        """Fulfil the bridge's XHR for a dialog it caught (failures swallowed, so the page
        unblocks), else answer natively over CDP (``promptText`` only for a prompt, when
        given); raises when CDP refuses."""
        session_id = dialog.cdp_session_id or None
        if dialog.bridge_request_id:
            body = json.dumps(
                {
                    "accept": bool(accept),
                    "dialog_id": dialog.id,
                    "prompt_text": (prompt_text or "") if dialog.type == "prompt" else "",
                }
            ).encode()
            await self._cdp_quiet(
                "Fetch.fulfillRequest",
                {
                    "requestId": dialog.bridge_request_id,
                    "responseCode": 200,
                    "responseHeaders": [
                        {"name": "Content-Type", "value": "application/json"},
                        {"name": "Access-Control-Allow-Origin", "value": "*"},
                    ],
                    "body": base64.b64encode(body).decode(),
                },
                session_id=session_id,
                timeout=5.0,
                what=f"bridge fulfill {dialog.id}",
            )
            return
        params: dict[str, Any] = {"accept": accept}
        if prompt_text is not None and dialog.type == "prompt":
            params["promptText"] = prompt_text
        await self._cdp("Page.handleJavaScriptDialog", params, session_id=session_id, timeout=5.0)

    async def _respond_quiet(
        self, dialog: PendingDialog, *, accept: bool, prompt_text: str | None
    ) -> None:
        """An auto policy's or the watchdog's answer (already archived); failures logged only."""
        try:
            await self._respond(dialog, accept=accept, prompt_text=prompt_text)
        except Exception as e:  # noqa: BLE001
            logger.debug("auto response failed for %s: %s", dialog.id, e)

    async def _handle_dialog_cdp(
        self, dialog: PendingDialog, *, accept: bool, prompt_text: str
    ) -> None:
        """The agent's answer. The dialog is retired whatever the outcome: a CDP error usually
        means it already closed (the browser dismissed it on a navigation)."""
        try:
            await self._respond(dialog, accept=accept, prompt_text=prompt_text)
        finally:
            self._retire_dialog(dialog.id, "agent", accepted=accept)

    async def _dialog_timeout_expired(self, dialog_id: str) -> None:
        dialog = self._pending_dialogs.get(dialog_id)
        if dialog is None:
            return
        logger.warning(
            "CDP supervisor %s: dialog %s (%s) dismissed after %ss unanswered",
            self.task_id,
            dialog_id,
            dialog.type,
            self.dialog_timeout_s,
        )
        self._retire_dialog(
            dialog_id, "watchdog", accepted=False
        )  # archived BEFORE the page unblocks
        await self._respond_quiet(dialog, accept=False, prompt_text=None)

    # Bookkeeping ---------------------------------------------------------------

    def _retire_dialog(
        self, dialog_id: str, closed_by: str, *, accepted: bool | None = None
    ) -> None:
        """Remove a pending dialog (archiving it with ``closed_by``) and cancel its watchdog."""
        dialog = self._pending_dialogs.pop(dialog_id, None)
        if dialog is not None:
            self._archive_dialog(dialog, closed_by, accepted=accepted)
        handle = self._dialog_watchdogs.pop(dialog_id, None)
        if handle is not None:
            handle.cancel()

    def _archive_dialog(
        self, dialog: PendingDialog, closed_by: str, *, accepted: bool | None = None
    ) -> None:
        """Move a dialog to the ``recent_dialogs`` ring buffer."""
        record = DialogRecord(
            id=dialog.id,
            type=dialog.type,
            message=dialog.message,
            opened_at=dialog.opened_at,
            closed_at=time.time(),
            closed_by=closed_by,
            frame_id=dialog.frame_id,
            accepted=accepted,
        )
        self._recent_dialogs = _trim_ring([*self._recent_dialogs, record], RECENT_DIALOGS_MAX)
        self._changed("closed", record)

    def _changed(self, what: str, dialog: PendingDialog | DialogRecord) -> None:
        if self._on_dialog_change is not None:
            self._on_dialog_change(what, dialog)

    async def _on_dialog_closed(self, params: dict[str, Any], session_id: str | None) -> None:
        # ``Page.javascriptDialogClosed`` carries only ``result``/``userInput``: match by
        # session id and clear the oldest native dialog on it (the JS thread blocks while a
        # dialog is up, so at most one is in flight). Bridge dialogs resolve through Fetch.
        candidate = next(
            (
                d.id
                for d in self._pending_dialogs.values()
                if d.cdp_session_id == session_id and d.bridge_request_id is None
            ),
            None,
        )
        if candidate:
            self._retire_dialog(candidate, "remote")

    # CDP event -> handler(self, params, session_id); merged into CDPSupervisor's.
    EVENT_HANDLERS: ClassVar[dict[str, Callable[..., Any]]] = {
        "Page.javascriptDialogOpening": _on_dialog_opening,
        "Page.javascriptDialogClosed": _on_dialog_closed,
        "Fetch.requestPaused": _on_fetch_paused,
    }
