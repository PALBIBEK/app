"""Screenshots on a hidden tab (``supervisor.hands.browser.shots``), Hermes's
``tests/tools/test_browser_use_shot.py`` on our runtime.

Chrome draws no frame for a hidden tab, and Browser Use cloud never answers a png
``Page.captureScreenshot`` for one: the fake page below times a png out while it is hidden,
as Playwright does past the deadline it is given, and records what was asked of it.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from supervisor.hands.browser import shots


class Timeout(Exception):
    """Playwright's TimeoutError, for the fake."""


class FakeCdp:
    def __init__(self, page: FakePage) -> None:
        self.page = page

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.page.calls.append({"method": method, **(params or {})})
        if method == "Page.getLayoutMetrics":
            return {
                "cssVisualViewport": {
                    "clientWidth": 1200,
                    "clientHeight": 600,
                    "pageX": 0,
                    "pageY": 0,
                },
                "cssContentSize": {"width": 1200, "height": 3000},
            }
        if method == "Page.captureScreenshot":
            return {"data": base64.b64encode(b"\x89PNG scaled").decode()}
        raise AssertionError(method)

    def detach(self) -> None:
        pass


class FakePage:
    def __init__(
        self, *, hidden: bool = True, activate_works: bool = True, png_hangs_hidden: bool = True
    ) -> None:
        self.hidden = hidden
        self.activate_works = activate_works
        self.png_hangs_hidden = png_hangs_hidden
        self.calls: list[dict[str, Any]] = []
        self.context = self

    def new_cdp_session(self, page: FakePage) -> FakeCdp:
        return FakeCdp(self)

    def evaluate(self, expression: str) -> str:
        self.calls.append({"method": "evaluate", "expression": expression})
        return "hidden" if self.hidden else "visible"

    def bring_to_front(self) -> None:
        self.calls.append({"method": "bring_to_front"})
        if not self.activate_works:
            raise RuntimeError("Page.bringToFront wasn't found")  # Lightpanda
        self.hidden = False

    def screenshot(self, *, path: str, type: str, timeout: float, **kw: Any) -> None:
        self.calls.append({"method": "screenshot", "type": type, "timeout": timeout, "path": path})
        if type == "png" and self.hidden and self.png_hangs_hidden:
            raise Timeout(f"Timeout {timeout:g}ms exceeded")
        Path(path).write_bytes(b"\x89PNG" if type == "png" else b"\xff\xd8 jpeg")


def screenshots(page: FakePage) -> list[dict[str, Any]]:
    return [c for c in page.calls if c["method"] == "screenshot"]


def test_a_hidden_tab_is_brought_to_the_front_so_the_png_answers_first_time(tmp_path: Path) -> None:
    page = FakePage()

    path = shots.capture_screenshot(page, tmp_path / "shot.png", timeout=Timeout)

    assert path == str(tmp_path / "shot.png") and Path(path).read_bytes() == b"\x89PNG"
    methods = [c["method"] for c in page.calls]
    assert methods.index("bring_to_front") < methods.index("screenshot")
    (png,) = screenshots(page)
    assert png["type"] == "png" and png["timeout"] <= shots.PNG_DEADLINE_S * 1000


def test_a_visible_tab_is_left_alone(tmp_path: Path) -> None:
    page = FakePage(hidden=False)

    shots.capture_screenshot(page, str(tmp_path / "shot.png"), timeout=Timeout)

    assert "bring_to_front" not in [c["method"] for c in page.calls]


@pytest.mark.parametrize("as_path", [False, True], ids=["str", "pathlib"])
def test_a_browser_that_cannot_activate_gets_one_bounded_png_then_a_jpeg(
    tmp_path: Path, as_path: bool
) -> None:
    page = FakePage(activate_works=False)
    asked = tmp_path / "shot.png"

    path = shots.capture_screenshot(page, asked if as_path else str(asked), timeout=Timeout)

    assert [s["type"] for s in screenshots(page)] == ["png", "jpeg"]
    assert screenshots(page)[0]["timeout"] <= 10_000, (
        "the png gets its short deadline, not the call's"
    )
    # No image library in the sandbox to re-encode: the jpeg goes beside it, named for what it is.
    assert path == str(tmp_path / "shot.jpg") and Path(path).read_bytes().startswith(b"\xff\xd8")


def test_a_capture_that_never_answers_says_how_long_it_waited(tmp_path: Path) -> None:
    page = FakePage(activate_works=False)
    page.screenshot = lambda **kw: (_ for _ in ()).throw(Timeout("late"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match=r"png after 10s, jpeg after 60s"):
        shots.capture_screenshot(page, str(tmp_path / "shot.png"), timeout=Timeout)


def test_max_dim_has_chrome_scale_the_capture(tmp_path: Path) -> None:
    page = FakePage(hidden=False)

    path = shots.capture_screenshot(page, str(tmp_path / "shot.png"), max_dim=600, timeout=Timeout)

    (capture,) = [c for c in page.calls if c["method"] == "Page.captureScreenshot"]
    assert capture["clip"]["scale"] == pytest.approx(0.5) and capture["format"] == "png"
    assert Path(path).read_bytes() == b"\x89PNG scaled"


def test_max_dim_above_the_page_is_the_plain_capture(tmp_path: Path) -> None:
    page = FakePage(hidden=False)

    shots.capture_screenshot(page, str(tmp_path / "shot.png"), max_dim=1800, timeout=Timeout)

    assert [s["type"] for s in screenshots(page)] == ["png"]


def test_a_small_capture_is_attached_as_it_is(tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG small")

    assert shots.attachable(
        FakePage(hidden=False), str(shot), False, str(tmp_path / "copy.jpg")
    ) == str(shot)


def test_a_large_capture_or_one_outside_the_workspace_is_attached_as_a_jpeg_copy(
    tmp_path: Path,
) -> None:
    large = tmp_path / "large.png"
    large.write_bytes(b"\x89PNG" + b"\0" * shots.MAX_ATTACHED_BYTES)
    small = tmp_path / "small.png"
    small.write_bytes(b"\x89PNG")

    for path, reuse in ((large, True), (small, False)):
        page = FakePage(hidden=False)
        copy = tmp_path / f"copy-{path.stem}.jpg"
        assert shots.attachable(page, str(path), False, str(copy), reuse=reuse) == str(copy)
        assert screenshots(page)[0]["type"] == "jpeg" and copy.read_bytes().startswith(b"\xff\xd8")


def test_a_new_tab_survives_a_browser_rejecting_activation() -> None:
    assert shots.activate(FakePage(activate_works=True)) is True
    assert shots.activate(FakePage(activate_works=False)) is False
