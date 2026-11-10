"""Screenshots and new tabs that never hang on a hidden tab: Hermes's ``browser_use_shot.py``.

Chrome draws no compositor frame for a hidden tab, and some hosted browsers (Browser Use
cloud) never answer ``Page.captureScreenshot`` with ``format: "png"`` for one, so a capture
waits for its whole timeout. Here a png gets ``PNG_DEADLINE_S``, then a jpeg (which a hidden
tab still answers, in a few seconds) gets ``JPEG_DEADLINE_S``, and a hidden tab is brought
to the front first, as a new tab is: Hermes does both only on a browser no human watches,
which every browser here is (the sandbox's own or a hosted one; a person's own Chrome is
never driven), and the live view follows the tab the agent works in.

Part of the sandbox runtime (``runner.py`` loads it by path): the standard library only,
and the Playwright page it is handed.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

PNG_DEADLINE_S = 10.0
"""A png that has not arrived by then will not: a visible tab answers in well under a second."""
JPEG_DEADLINE_S = 60.0
JPEG_QUALITY = 90
ATTACH_QUALITY = 70
"""JPEG quality of the copy attached for the model when the png is too large to attach."""
MAX_ATTACHED_BYTES = 384 * 1024
"""The largest screenshot a result attaches (``browser_exec.MAX_SHOWN_BYTES``)."""
EMBED_MAX_DIMENSION = 1568
"""The longest side of an attached copy (Hermes's ``_EMBED_MAX_DIMENSION``): what vision
models take without scaling it down themselves."""


def bring_to_front_if_hidden(page: Any) -> None:
    """Activate ``page`` when it reports it is hidden; a visible tab is left alone."""
    try:
        if page.evaluate("document.visibilityState") == "hidden":
            page.bring_to_front()
    # An engine without Page.bringToFront (Lightpanda), or a page mid-navigation: capture anyway.
    except Exception:  # noqa: BLE001, S110 — the capture below is what answers
        pass


def _capture(page: Any, path: str, full: bool, timeout: type[BaseException]) -> str:
    """``path`` written with the viewport (or the whole page): png within its deadline, else
    jpeg. The file's name says what it holds, so a jpeg asked for at ``.png`` goes beside it
    at ``.jpg`` (the sandbox has no image library to re-encode it); the path written."""
    try:
        page.screenshot(path=path, type="png", full_page=full, timeout=PNG_DEADLINE_S * 1000)
        return path
    except timeout:
        pass
    target = str(Path(path).with_suffix(".jpg")) if path.lower().endswith(".png") else path
    try:
        page.screenshot(
            path=target,
            type="jpeg",
            quality=JPEG_QUALITY,
            full_page=full,
            timeout=JPEG_DEADLINE_S * 1000,
        )
    except timeout as exc:
        raise RuntimeError(
            f"Page.captureScreenshot timed out (png after {PNG_DEADLINE_S:g}s, jpeg after {JPEG_DEADLINE_S:g}s)"
        ) from exc
    return target


def _scaled(
    page: Any, path: str, full: bool, max_dim: int, quality: int | None = None
) -> str | None:
    """``path`` written as a png (a jpeg at ``quality``) whose longer side is at most
    ``max_dim``, by Chrome itself (a clip with a scale); None when the page is no larger,
    for the plain capture."""
    client = page.context.new_cdp_session(page)
    try:
        metrics = client.send("Page.getLayoutMetrics")
        box = metrics.get("cssContentSize" if full else "cssVisualViewport") or {}
        width, height = (
            float(box.get("width") or box.get("clientWidth") or 0),
            float(box.get("height") or box.get("clientHeight") or 0),
        )
        if max(width, height) <= max_dim or not width or not height:
            return None
        scale = max_dim / max(width, height)
        origin = (0.0, 0.0) if full else (float(box.get("pageX", 0)), float(box.get("pageY", 0)))
        shot = client.send(
            "Page.captureScreenshot",
            {
                "format": "png" if quality is None else "jpeg",
                **({} if quality is None else {"quality": quality}),
                "captureBeyondViewport": full,
                "clip": {
                    "x": origin[0],
                    "y": origin[1],
                    "width": width,
                    "height": height,
                    "scale": scale,
                },
            },
        )
    finally:
        client.detach()
    Path(path).write_bytes(base64.b64decode(shot["data"]))
    return path


def capture_screenshot(
    page: Any,
    path: str,
    *,
    full: bool = False,
    max_dim: int | None = None,
    timeout: type[BaseException] = TimeoutError,
) -> str:
    """Save a screenshot of ``page`` at ``path`` (a ``pathlib.Path`` works too); the path
    written. ``max_dim`` bounds the longer side (1800 keeps a 2x display's capture under the
    2000 px a side some image-aware models enforce). ``timeout`` is the Playwright
    ``TimeoutError`` a capture past its deadline raises."""
    path = os.fspath(path)
    bring_to_front_if_hidden(page)
    if max_dim and (scaled := _scaled(page, path, full, int(max_dim))):
        return scaled
    return _capture(page, path, full, timeout)


def attachable(page: Any, path: str, full: bool, copy: str, *, reuse: bool = True) -> str:
    """What of a capture at ``path`` the model is shown: the capture itself when it is small
    enough to attach (and ``reuse``: it is where the result can read it), else a JPEG of the
    same view at ``copy`` (Hermes attaches a capture re-encoded as a JPEG whose longer side
    is at most ``EMBED_MAX_DIMENSION``)."""
    if reuse and os.path.getsize(path) <= MAX_ATTACHED_BYTES:
        return path
    if not _scaled(page, copy, full, EMBED_MAX_DIMENSION, quality=ATTACH_QUALITY):
        page.screenshot(
            path=copy,
            type="jpeg",
            quality=ATTACH_QUALITY,
            full_page=full,
            scale="css",
            timeout=JPEG_DEADLINE_S * 1000,
        )
    return copy


def activate(page: Any) -> bool:
    """Bring a new tab to the front, so it renders (and the live view shows it); whether it
    could be: a browser that refuses leaves it in the background, open all the same."""
    try:
        page.bring_to_front()
    except Exception:  # noqa: BLE001 — the tab exists either way
        return False
    return True
