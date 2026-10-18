"""Path confinement.

This is one of the two ways out of the sandbox (the other is the environment).
The tests are mostly adversarial, because the failure mode is silent: a
traversal that resolves outside the root reads the user's real files and
nothing looks wrong.
"""

from __future__ import annotations

import pytest

from githi.errors import SandboxViolation
from githi.sandbox.paths import resolve_in_root
from githi.sandbox.policy import SandboxPolicy


@pytest.fixture
def policy(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_text("hi")
    return SandboxPolicy(root=tmp_path)


class TestAccepts:
    def test_relative_path_inside_root(self, policy):
        assert resolve_in_root("sub/file.txt", policy).read_text() == "hi"

    def test_absolute_path_inside_root(self, policy):
        target = policy.root / "sub" / "file.txt"
        assert resolve_in_root(str(target), policy) == target

    def test_the_root_itself(self, policy):
        assert resolve_in_root(".", policy) == policy.root

    def test_path_that_does_not_exist_yet(self, policy):
        """`write` must be able to name a file before creating it -- so
        confinement cannot depend on the target already existing."""
        assert resolve_in_root("sub/new.txt", policy) == policy.root / "sub" / "new.txt"


class TestRejects:
    @pytest.mark.parametrize("attempt", [
        "../outside.txt",
        "sub/../../outside.txt",
        "/etc/passwd",
        "sub/../../../../../../etc/passwd",
        "..",
    ])
    def test_traversal_attempts(self, policy, attempt):
        with pytest.raises(SandboxViolation):
            resolve_in_root(attempt, policy)

    def test_symlink_pointing_outside_root(self, policy, tmp_path_factory):
        """The important one. `..` is easy to catch; a symlink that *resolves*
        outside is the version that slips through a string-only check."""
        outside = tmp_path_factory.mktemp("outside")
        secret = outside / "secret.txt"
        secret.write_text("private")
        link = policy.root / "escape"
        link.symlink_to(secret)

        with pytest.raises(SandboxViolation):
            resolve_in_root("escape", policy)

    def test_symlinked_directory_pointing_outside_root(self, policy, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside2")
        (outside / "f.txt").write_text("private")
        (policy.root / "linkdir").symlink_to(outside, target_is_directory=True)

        with pytest.raises(SandboxViolation):
            resolve_in_root("linkdir/f.txt", policy)

    def test_error_names_the_offending_path(self, policy):
        with pytest.raises(SandboxViolation, match="outside.txt"):
            resolve_in_root("../outside.txt", policy)


def test_sibling_directory_with_shared_prefix_is_not_inside(tmp_path):
    """A pure string `startswith` check would wrongly accept `/tmp/rootEVIL`
    as being inside `/tmp/root`."""
    root = tmp_path / "root"
    root.mkdir()
    evil = tmp_path / "rootEVIL"
    evil.mkdir()
    (evil / "f.txt").write_text("x")

    with pytest.raises(SandboxViolation):
        resolve_in_root(str(evil / "f.txt"), SandboxPolicy(root=root))
