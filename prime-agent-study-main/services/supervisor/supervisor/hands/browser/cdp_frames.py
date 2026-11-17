"""Frame and out-of-process iframe tracking, the half of the CDP supervisor
(``cdp_supervisor.py``) that knows the page's frames.

Hermes's ``tools/browser_supervisor_frames.py``, on the daemon's loop. Keeps
``CDPSupervisor._frames`` from ``Page.frame*`` and ``Target.*`` events and renders the
bounded ``frame_tree``. An out-of-process iframe (an iframe of another site, in a process
of its own) carries its child CDP session id, so ``browser_cdp(frame_id=...)`` can send
calls into it over the supervisor's live socket.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, ClassVar

logger = logging.getLogger("githi.browser.supervisor")

FRAME_TREE_MAX_ENTRIES = 30
"""Frames a ``frame_tree`` lists (an ad-heavy page has hundreds)."""
FRAME_TREE_MAX_OOPIF_DEPTH = 2

_AUTO_ATTACH_PARAMS = {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}


@dataclass
class FrameInfo:
    """One frame of a page. An ``is_oopif`` frame has a CDP target of its own (reached
    through ``cdp_session_id``); a same-origin or srcdoc iframe shares its parent's process
    (``is_oopif=False``, ``cdp_session_id`` its page's)."""

    frame_id: str
    url: str
    origin: str
    parent_frame_id: str | None
    is_oopif: bool
    cdp_session_id: str | None = None
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "frame_id": self.frame_id,
            "url": self.url,
            "origin": self.origin,
            "is_oopif": self.is_oopif,
        }
        optional = (
            ("session_id", self.cdp_session_id),
            ("parent_frame_id", self.parent_frame_id),
            ("name", self.name),
        )
        d.update({k: v for k, v in optional if v})
        return d


class FrameTrackingMixin:
    """Frame-tree bookkeeping for ``CDPSupervisor`` (its event handlers run on its loop)."""

    _frames: dict[str, FrameInfo]

    async def _cdp(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float | None = 10.0,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _install_dialog_bridge(self, session_id: str) -> None:
        raise NotImplementedError

    async def _enable_page_domains(self, session_id: str | None, *, timeout: float) -> None:
        """Page.enable, Runtime.enable and nested auto-attach on one session."""
        await self._cdp("Page.enable", session_id=session_id, timeout=timeout)
        await self._cdp("Runtime.enable", session_id=session_id, timeout=timeout)
        await self._cdp(
            "Target.setAutoAttach", _AUTO_ATTACH_PARAMS, session_id=session_id, timeout=timeout
        )

    async def _seed_frames(self, session_id: str, *, timeout: float) -> None:
        """The frames a session's page has already: events tell only of later changes, and a
        page whose content was written in (not navigated to) sends none for its main frame."""
        try:
            tree = (
                (await self._cdp("Page.getFrameTree", session_id=session_id, timeout=timeout))
                .get("result", {})
                .get("frameTree")
            )
        except Exception as e:  # noqa: BLE001 — the page went
            logger.debug("reading the frames of %s failed: %s", session_id[:16], e)
            return
        stack = [tree] if tree else []
        while stack:
            node = stack.pop()
            frame = node.get("frame") or {}
            if frame_id := frame.get("id"):
                old = self._frames.get(frame_id)
                self._frames[frame_id] = FrameInfo(
                    frame_id=frame_id,
                    url=str(frame.get("url") or ""),
                    origin=str(frame.get("securityOrigin") or ""),
                    parent_frame_id=frame.get("parentId") or (old.parent_frame_id if old else None),
                    is_oopif=old.is_oopif if old else False,
                    cdp_session_id=old.cdp_session_id if old and old.is_oopif else session_id,
                    name=str(frame.get("name") or ""),
                )
            stack.extend(node.get("childFrames") or [])

    def _on_frame_attached(self, params: dict[str, Any], session_id: str | None) -> None:
        frame_id = params.get("frameId")
        if frame_id:
            self._frames[frame_id] = FrameInfo(
                frame_id=frame_id,
                url="",
                origin="",
                parent_frame_id=params.get("parentFrameId"),
                is_oopif=False,
                cdp_session_id=session_id,
            )

    def _on_frame_navigated(self, params: dict[str, Any], session_id: str | None) -> None:
        frame = params.get("frame") or {}
        frame_id = frame.get("id")
        if not frame_id:
            return
        old = self._frames.get(frame_id) or FrameInfo(frame_id, "", "", None, False, session_id)
        self._frames[frame_id] = FrameInfo(
            frame_id=frame_id,
            url=str(frame.get("url") or ""),
            origin=str(frame.get("securityOrigin") or frame.get("origin") or ""),
            parent_frame_id=frame.get("parentId") or old.parent_frame_id,
            is_oopif=old.is_oopif,
            cdp_session_id=old.cdp_session_id,
            name=str(frame.get("name") or old.name),
        )

    def _on_frame_detached(self, params: dict[str, Any], session_id: str | None) -> None:
        """Drop a frame only when it is truly gone. ``reason="swap"`` is a frame moving to
        another process (made an out-of-process iframe, say): dropping it would hide the
        iframe. Even on ``remove`` the parent knows only that the child left ITS process; a
        live child session means it is still alive, kept until Target.detached and a later
        frameDetached clear it."""
        frame_id = params.get("frameId")
        if not frame_id or str(params.get("reason") or "remove").lower() == "swap":
            return
        old = self._frames.get(frame_id)
        if not (old and old.is_oopif and old.cdp_session_id):
            self._frames.pop(frame_id, None)

    async def _on_target_attached(
        self, params: dict[str, Any], session_id: str | None = None
    ) -> None:
        info = params.get("targetInfo") or {}
        sid = params.get("sessionId")
        target_type = info.get("type")
        if not sid or target_type not in {"iframe", "worker"}:
            return
        if target_type == "iframe":
            # The frame, with its own session for calls into it; its origin comes with the
            # frameNavigated its session sends.
            target_id = str(info.get("targetId") or "")
            old = self._frames.get(target_id)
            self._frames[target_id] = FrameInfo(
                frame_id=target_id,
                url=str(info.get("url") or ""),
                origin="",
                is_oopif=True,
                cdp_session_id=sid,
                parent_frame_id=old.parent_frame_id if old else None,
                name=str(info.get("title") or (old.name if old else "")),
            )
        # Off the reader: awaiting the replies here would deadlock, as only the reader
        # resolves them.
        asyncio.ensure_future(self._enable_child_domains(sid))

    async def _enable_child_domains(self, sid: str) -> None:
        """Page and Runtime (and nested auto-attach), then the dialog bridge, on a child session."""
        try:
            await self._enable_page_domains(sid, timeout=3.0)
        except Exception as e:  # noqa: BLE001
            logger.debug("child session %s setup failed: %s", sid[:16], e)
        await self._install_dialog_bridge(sid)
        await self._seed_frames(sid, timeout=3.0)

    def _on_target_detached(self, params: dict[str, Any], session_id: str | None = None) -> None:
        """Clear the session of frames on a detached child session. The frames are NOT
        dropped: a hosted browser detaches for a moment while a page changes and the iframe
        is still there; ``Page.frameDetached`` cleans up when it truly goes."""
        sid = params.get("sessionId")
        if not sid:
            return
        self._frames.update(
            {
                fid: replace(f, cdp_session_id=None)
                for fid, f in self._frames.items()
                if f.cdp_session_id == sid
            }
        )

    def _on_target_info_changed(
        self, params: dict[str, Any], session_id: str | None = None
    ) -> None:
        """An out-of-process iframe's URL, which its target knows before its frame says it."""
        info = params.get("targetInfo") or {}
        frame = self._frames.get(str(info.get("targetId") or ""))
        if frame is not None and frame.is_oopif and info.get("url"):
            self._frames[frame.frame_id] = replace(frame, url=str(info["url"]))

    def _build_frame_tree(self) -> dict[str, Any]:
        """The capped ``frame_tree``: the top frame (one with no parent, preferring a page's
        own), then breadth first from it, at most FRAME_TREE_MAX_ENTRIES, out-of-process
        branches at most FRAME_TREE_MAX_OOPIF_DEPTH deep."""
        frames = self._frames
        tops = [f for f in frames.values() if not f.parent_frame_id]
        top = next((f for f in tops if not f.is_oopif), tops[0] if tops else None)
        if top is None:
            return {"top": None, "children": [], "truncated": False}
        children: list[dict[str, Any]] = []
        truncated = False
        queue: list[tuple[FrameInfo, int]] = [
            (f, 1) for f in frames.values() if f.parent_frame_id == top.frame_id
        ]
        visited = {top.frame_id}
        while queue and len(children) < FRAME_TREE_MAX_ENTRIES:
            frame, depth = queue.pop(0)
            if frame.frame_id in visited:
                continue
            visited.add(frame.frame_id)
            if frame.is_oopif and depth > FRAME_TREE_MAX_OOPIF_DEPTH:
                truncated = True
                continue
            children.append(frame.to_dict())
            queue.extend(
                (f, depth + 1)
                for f in frames.values()
                if f.parent_frame_id == frame.frame_id and f.frame_id not in visited
            )
        return {"top": top.to_dict(), "children": children, "truncated": truncated or bool(queue)}

    # CDP event -> handler(self, params, session_id); merged into CDPSupervisor's.
    EVENT_HANDLERS: ClassVar[dict[str, Callable[..., Any]]] = {
        "Page.frameAttached": _on_frame_attached,
        "Page.frameNavigated": _on_frame_navigated,
        "Page.frameDetached": _on_frame_detached,
        "Target.attachedToTarget": _on_target_attached,
        "Target.detachedFromTarget": _on_target_detached,
        "Target.targetInfoChanged": _on_target_info_changed,
    }
