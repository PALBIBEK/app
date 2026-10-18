"""Skills, with progressive disclosure.

The property that matters: a library of fifty skills costs fifty *lines* of
system prompt, not fifty skill bodies. Names and one-line descriptions are
always present; bodies load only when the model asks. Without this, adding
skills makes every turn of every run more expensive forever.
"""

from __future__ import annotations

import pytest

from githi.harness.skills import Skill, SkillLibrary

SKILL_MD = """\
---
name: github-duplicates
description: Finding issues that report the same underlying bug.
---
# Body

Narrow first, verify with full bodies, never trust keyword overlap.
"""


@pytest.fixture
def library(tmp_path):
    d = tmp_path / "skills" / "github-duplicates"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD)

    other = tmp_path / "skills" / "triage"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text(
        "---\nname: triage\ndescription: Grouping issues.\n---\nbody here\n")
    return SkillLibrary.discover([tmp_path / "skills"])


class TestDiscovery:
    def test_finds_all_skills(self, library):
        assert {s.name for s in library.skills} == {"github-duplicates", "triage"}

    def test_parses_frontmatter(self, library):
        skill = library.get("github-duplicates")
        assert skill.description.startswith("Finding issues")

    def test_body_excludes_frontmatter(self, library):
        body = library.get("github-duplicates").body
        assert "Narrow first" in body and "description:" not in body

    def test_missing_directory_is_not_an_error(self, tmp_path):
        """A user skills dir that does not exist yet is normal."""
        assert SkillLibrary.discover([tmp_path / "absent"]).skills == []

    def test_malformed_skill_is_skipped_not_fatal(self, tmp_path):
        d = tmp_path / "skills" / "broken"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("no frontmatter at all")
        assert SkillLibrary.discover([tmp_path / "skills"]).skills == []

    def test_later_roots_do_not_override_earlier_ones(self, tmp_path):
        """Discovery order is precedence: bundled skills win over user ones
        with the same name, so a stray local file cannot silently replace a
        shipped playbook."""
        for root, desc in (("a", "first"), ("b", "second")):
            d = tmp_path / root / "dup"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: dup\ndescription: {desc}\n---\nx\n")
        lib = SkillLibrary.discover([tmp_path / "a", tmp_path / "b"])
        assert lib.get("dup").description == "first"


class TestProgressiveDisclosure:
    def test_listing_has_names_and_descriptions(self, library):
        listing = library.listing()
        assert "github-duplicates" in listing and "Finding issues" in listing

    def test_listing_excludes_bodies(self, library):
        """The entire point. If bodies leaked into the listing, the system
        prompt would grow with the skill library."""
        assert "Narrow first" not in library.listing()

    def test_listing_is_empty_when_no_skills(self, tmp_path):
        assert SkillLibrary.discover([tmp_path / "none"]).listing() == ""


class TestPiParity:
    """Pi ships no skill tool: it advertises each skill's file path and the
    model opens it with the ordinary `read` tool. One less tool in every
    request, and skills stay ordinary files that `grep` and `read` already
    understand."""

    def test_listing_uses_pis_xml_envelope(self, library):
        listing = library.listing()
        assert "<available_skills>" in listing and "</available_skills>" in listing
        assert "<name>github-duplicates</name>" in listing

    def test_listing_gives_a_readable_location(self, library, tmp_path):
        from githi.tools.base import ToolContext
        from githi.sandbox.policy import SandboxPolicy
        from githi.tools.read import read_tool

        skill = library.get("github-duplicates")
        assert skill.path.endswith("SKILL.md")
        # The advertised path must actually work in `read`.
        ctx = ToolContext(policy=SandboxPolicy(root=tmp_path))
        assert "Narrow first" in read_tool().run({"path": skill.path}, ctx).text

    def test_listing_tells_the_model_to_read_the_file(self, library):
        assert "read the full skill file" in library.listing().lower()

    def test_xml_special_characters_are_escaped(self, tmp_path):
        d = tmp_path / "s" / "x"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: x\ndescription: uses <tags> & \"quotes\"\n---\nbody\n")
        listing = SkillLibrary.discover([tmp_path / "s"]).listing()
        assert "&lt;tags&gt;" in listing and "&amp;" in listing


def test_skill_dataclass_roundtrip():
    s = Skill(name="n", description="d", body="b", path="p")
    assert (s.name, s.description, s.body, s.path) == ("n", "d", "b", "p")
