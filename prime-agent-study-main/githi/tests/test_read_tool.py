"""The read tool's pagination and image support (port of `harness/tools/read.ts`).

Two gaps against pi, both of which cost the agent a turn or a whole capability.

**Pagination.** githi truncates at a character count and stops. Faced with a
5,000-line file the model gets the first chunk and a dead end -- there is no
parameter that would let it ask for the rest. pi truncates at whichever of
2,000 lines or 50KB comes first and then *tells the model the offset to
resume from*, which turns a wall into a loop it can drive.

**Images.** githi decodes every file as UTF-8 with `errors="replace"`. Handed a
PNG it emits a screenful of U+FFFD and reports success, so the model cannot
see the image and cannot tell that it failed to. Detection is by content, so
the tool is never fooled by a wrong or missing extension.
"""

from __future__ import annotations

import base64

import pytest

from githi.sandbox.policy import SandboxPolicy
from githi.tools.base import ToolContext
from githi.tools.read import read_tool
from githi.types import ImageContent, TextContent

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def _png_bytes() -> bytes:
    ihdr = (13).to_bytes(4, "big") + b"IHDR" + b"\x00" * 13 + b"\x00" * 4
    return PNG_SIGNATURE + ihdr


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "big.txt").write_text(
        "".join(f"line{i}\n" for i in range(1, 5001)), encoding="utf-8")
    (tmp_path / "small.txt").write_text("a\nb\nc\n", encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(_png_bytes())
    return ToolContext(policy=SandboxPolicy(root=tmp_path))


class TestPagination:
    def test_offset_starts_at_the_requested_line(self, ctx):
        result = read_tool().run({"path": "small.txt", "offset": 2}, ctx)
        assert "b" in result.text and "\ta\n" not in result.text

    def test_offset_is_one_indexed(self, ctx):
        """Line numbers in the output are 1-indexed, so the offset that
        addresses them must be too -- a 0-indexed offset would silently be
        off by one against the numbers the model just read."""
        result = read_tool().run({"path": "small.txt", "offset": 1}, ctx)
        assert "a" in result.text

    def test_limit_caps_the_line_count(self, ctx):
        result = read_tool().run({"path": "big.txt", "offset": 1, "limit": 5}, ctx)
        body = [ln for ln in result.text.splitlines() if "\tline" in ln]
        assert len(body) == 5

    def test_line_numbers_reflect_the_offset(self, ctx):
        """Numbers must be absolute positions in the file. Restarting at 1 for
        every page makes them useless for locating anything."""
        result = read_tool().run({"path": "big.txt", "offset": 100, "limit": 2}, ctx)
        assert "100" in result.text and "line100" in result.text

    def test_offset_past_the_end_is_an_error(self, ctx):
        result = read_tool().run({"path": "small.txt", "offset": 99}, ctx)
        assert result.is_error and "3" in result.text

    def test_truncation_names_the_offset_to_resume_from(self, ctx):
        """The difference between a wall and a loop the model can drive."""
        result = read_tool().run({"path": "big.txt"}, ctx)
        assert "offset=" in result.text

    def test_default_read_is_capped(self, ctx):
        result = read_tool().run({"path": "big.txt"}, ctx)
        body = [ln for ln in result.text.splitlines() if "\tline" in ln]
        assert len(body) <= 2000

    def test_a_short_file_is_not_annotated(self, ctx):
        result = read_tool().run({"path": "small.txt"}, ctx)
        assert "offset=" not in result.text


class TestImages:
    def test_an_image_returns_an_image_block(self, ctx):
        result = read_tool().run({"path": "shot.png"}, ctx)
        assert any(isinstance(b, ImageContent) for b in result.content)

    def test_the_image_block_carries_base64_and_mime_type(self, ctx):
        result = read_tool().run({"path": "shot.png"}, ctx)
        block = next(b for b in result.content if isinstance(b, ImageContent))
        assert block.mime_type == "image/png"
        assert base64.b64decode(block.data) == _png_bytes()

    def test_the_image_is_labelled_in_text(self, ctx):
        """The model needs to know an attachment arrived and what it is."""
        result = read_tool().run({"path": "shot.png"}, ctx)
        text = " ".join(b.text for b in result.content if isinstance(b, TextContent))
        assert "image/png" in text

    def test_detection_ignores_the_extension(self, ctx):
        """The extension is a claim by whoever named the file; the magic bytes
        are the file."""
        (ctx.policy.root / "mislabelled.txt").write_bytes(_png_bytes())
        result = read_tool().run({"path": "mislabelled.txt"}, ctx)
        assert any(isinstance(b, ImageContent) for b in result.content)

    def test_a_text_file_named_png_is_still_text(self, ctx):
        (ctx.policy.root / "fake.png").write_text("just text\n", encoding="utf-8")
        result = read_tool().run({"path": "fake.png"}, ctx)
        assert not any(isinstance(b, ImageContent) for b in result.content)
        assert "just text" in result.text

    def test_unrenderable_binary_is_reported_not_mangled(self, ctx):
        """Decoding a binary as UTF-8 yields replacement characters and a
        success flag -- the model burns a turn on garbage it cannot identify
        as garbage."""
        (ctx.policy.root / "app.bin").write_bytes(bytes(range(256)) * 40)
        result = read_tool().run({"path": "app.bin"}, ctx)
        assert "binary" in result.text.lower()
        assert "�" not in result.text
