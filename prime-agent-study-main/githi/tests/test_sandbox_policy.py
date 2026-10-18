"""SandboxPolicy -- the single object that says what a tool may do.

Everything enforceable lives here so a reviewer can read one dataclass and
know the blast radius, rather than reconstructing it from checks scattered
across eight tool implementations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from githi.sandbox.policy import SandboxPolicy


class TestDefaults:
    def test_default_is_restrictive(self, tmp_path):
        """An unconfigured sandbox must not be a permissive one. Anyone who
        forgets to pass a policy should get the safe thing."""
        p = SandboxPolicy(root=tmp_path)
        assert p.allow_network is False
        assert p.allow_credentials is False
        assert p.writable is True          # inside root only
        assert p.timeout_s > 0
        assert p.max_output_bytes > 0

    def test_root_is_resolved_absolute(self, tmp_path):
        """Path confinement compares resolved paths; a relative or symlinked
        root would make every later comparison unreliable."""
        p = SandboxPolicy(root=Path("."))
        assert p.root.is_absolute()

    def test_read_only_policy(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, writable=False)
        assert p.writable is False


class TestTimeouts:
    def test_timeout_is_capped(self, tmp_path):
        """A model asking for an hour must not get one."""
        p = SandboxPolicy(root=tmp_path, timeout_s=10_000)
        assert p.timeout_s == p.max_timeout_s

    def test_requested_timeout_below_cap_is_honoured(self, tmp_path):
        assert SandboxPolicy(root=tmp_path, timeout_s=5).timeout_s == 5

    def test_non_positive_timeout_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            SandboxPolicy(root=tmp_path, timeout_s=0)


class TestCredentialPolicy:
    def test_secret_patterns_cover_the_usual_suspects(self, tmp_path):
        p = SandboxPolicy(root=tmp_path)
        for name in ("GITHUB_TOKEN", "OPENROUTER_API_KEY", "AWS_SECRET_ACCESS_KEY",
                     "MY_PASSWORD", "SOME_CREDENTIAL", "GH_TOKEN"):
            assert p.is_secret(name), name

    def test_ordinary_vars_are_not_secrets(self, tmp_path):
        p = SandboxPolicy(root=tmp_path)
        for name in ("PATH", "HOME", "LANG", "TERM", "PWD"):
            assert not p.is_secret(name), name

    def test_matching_is_case_insensitive(self, tmp_path):
        assert SandboxPolicy(root=tmp_path).is_secret("github_token")

    def test_allow_credentials_disables_scrubbing(self, tmp_path):
        """Explicit opt-in exists for a trusted, non-model-driven caller --
        but it must be a deliberate act, never the default."""
        assert SandboxPolicy(root=tmp_path, allow_credentials=True).is_secret("GITHUB_TOKEN") is False

    def test_extra_secret_names_can_be_added(self, tmp_path):
        p = SandboxPolicy(root=tmp_path, extra_secrets=("COMPANY_INTERNAL",))
        assert p.is_secret("COMPANY_INTERNAL")


class TestOutputLimits:
    def test_output_cap_is_enforced_as_a_number(self, tmp_path):
        assert SandboxPolicy(root=tmp_path, max_output_bytes=100).max_output_bytes == 100

    def test_non_positive_output_cap_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            SandboxPolicy(root=tmp_path, max_output_bytes=0)
