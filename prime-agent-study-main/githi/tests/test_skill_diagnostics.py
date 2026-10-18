"""A skill that fails to load must say so.

Probed, not tested into existence: four skill directories went in, two came
back, and nothing anywhere reported the other two. The user's experience is
that they wrote a skill and it simply is not there -- no error, no warning,
no way to tell a typo from a path mistake from a file githi never looked at.

Worse than the silent drop is the silent *partial* load. A file with `nmae:`
instead of `name:` currently registers under its directory name with an empty
description, so it appears in the listing with nothing telling the model when
to use it -- an invisible skill that costs prompt tokens on every turn.

pi returns typed diagnostic codes for exactly this. Loading stays lenient: one
broken skill must not prevent the others from loading, because the fix would
otherwise be to delete a file to get the rest back.
"""

from __future__ import annotations

import pytest

from githi.harness.skills import SkillLibrary


def _write(root, name, body):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


@pytest.fixture
def skills(tmp_path):
    root = tmp_path / "skills"
    _write(root, "good", "---\nname: good\ndescription: works\n---\nbody\n")
    _write(root, "nofm", "no frontmatter at all\n")
    _write(root, "typo", "---\nnmae: typo\ndescription: d\n---\nbody\n")
    _write(root, "nodesc", "---\nname: nodesc\n---\nbody\n")
    return root


class TestValidSkillsStillLoad:
    def test_the_good_one_loads(self, skills):
        library = SkillLibrary.discover([skills])
        assert "good" in [s.name for s in library.skills]

    def test_one_broken_skill_does_not_block_the_others(self, skills):
        """Otherwise the fix is to delete a file to get the rest back."""
        assert SkillLibrary.discover([skills]).skills


class TestDiagnostics:
    def test_failures_are_reported(self, skills):
        assert SkillLibrary.discover([skills]).diagnostics

    def test_a_missing_frontmatter_block_is_named(self, skills):
        library = SkillLibrary.discover([skills])
        assert any("nofm" in d.path for d in library.diagnostics)

    def test_a_missing_description_is_reported(self, skills):
        """It loads, but with nothing to tell the model when to use it -- so
        it costs prompt tokens and buys nothing."""
        library = SkillLibrary.discover([skills])
        assert any("nodesc" in d.path and "description" in d.message.lower()
                   for d in library.diagnostics)

    def test_a_missing_name_is_reported(self, skills):
        library = SkillLibrary.discover([skills])
        assert any("typo" in d.path and "name" in d.message.lower()
                   for d in library.diagnostics)

    def test_a_diagnostic_carries_a_code(self, skills):
        """A code, not just prose: a caller may want to warn on parse errors
        while ignoring merely-incomplete skills."""
        assert all(d.code for d in SkillLibrary.discover([skills]).diagnostics)

    def test_a_diagnostic_points_at_the_file(self, skills):
        library = SkillLibrary.discover([skills])
        assert all(d.path.endswith("SKILL.md") for d in library.diagnostics)

    def test_a_clean_directory_reports_nothing(self, tmp_path):
        root = tmp_path / "skills"
        _write(root, "good", "---\nname: good\ndescription: works\n---\nbody\n")
        assert SkillLibrary.discover([root]).diagnostics == []

    def test_an_absent_directory_is_not_an_error(self, tmp_path):
        """A user skills directory that does not exist is the normal case,
        not a misconfiguration."""
        library = SkillLibrary.discover([tmp_path / "nope"])
        assert library.diagnostics == [] and library.skills == []


class TestReporting:
    def test_there_is_a_human_readable_summary(self, skills):
        text = SkillLibrary.discover([skills]).diagnostics_report()
        assert "nofm" in text and "SKILL.md" in text

    def test_the_summary_is_empty_when_all_is_well(self, tmp_path):
        root = tmp_path / "skills"
        _write(root, "good", "---\nname: good\ndescription: works\n---\nbody\n")
        assert SkillLibrary.discover([root]).diagnostics_report() == ""
