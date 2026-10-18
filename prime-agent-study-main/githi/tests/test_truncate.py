"""Output bounding."""

from __future__ import annotations

from githi.harness.truncate import truncate_lines, truncate_text


class TestTruncateText:
    def test_short_text_untouched(self):
        assert truncate_text("hello", 100) == ("hello", False)

    def test_long_text_is_cut(self):
        body, truncated = truncate_text("x" * 1000, 100)
        assert truncated is True and len(body) < 1000

    def test_keeps_both_ends(self):
        """The tail carries the answer more often than the head -- a
        command's result, an exception line, a file's conclusion."""
        body, _ = truncate_text("START" + "x" * 5000 + "END", 200)
        assert body.startswith("START") and body.endswith("END")

    def test_says_how_much_was_dropped(self):
        body, _ = truncate_text("x" * 5000, 200)
        assert "elided" in body

    def test_zero_limit_is_a_no_op_not_a_wipe(self):
        """A misconfigured limit should degrade to 'no truncation', never to
        'discard everything'."""
        assert truncate_text("abc", 0) == ("abc", False)


class TestTruncateLines:
    def test_under_limit(self):
        assert truncate_lines(["a", "b"], 10) == (["a", "b"], False)

    def test_over_limit(self):
        lines, truncated = truncate_lines([str(i) for i in range(50)], 10)
        assert truncated is True and len(lines) == 10

    def test_exact_limit_is_not_truncated(self):
        assert truncate_lines(["a", "b"], 2)[1] is False
