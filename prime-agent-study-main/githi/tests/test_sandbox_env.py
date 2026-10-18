"""Environment scrubbing -- the other way out of the sandbox.

Filtering by *pattern* rather than by an explicit denylist of known variables
is the whole point: a denylist silently leaks the next credential anyone adds
to the environment.
"""

from __future__ import annotations

import pytest

from githi.sandbox.env import build_child_env
from githi.sandbox.policy import SandboxPolicy


@pytest.fixture
def policy(tmp_path):
    return SandboxPolicy(root=tmp_path)


class TestScrubbing:
    @pytest.mark.parametrize("name", [
        "GITHUB_TOKEN", "GH_TOKEN", "OPENROUTER_API_KEY", "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AZURE_OPENAI_API_KEY",
        "DB_PASSWORD", "SERVICE_CREDENTIAL", "MY_SECRET",
    ])
    def test_secrets_are_removed(self, policy, monkeypatch, name):
        monkeypatch.setenv(name, "leak-me")
        assert name not in build_child_env(policy)

    def test_no_value_survives_even_under_a_different_name(self, policy, monkeypatch):
        """Scrubbing by key is only sound if no scrubbed *value* reappears
        elsewhere in the environment."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_unique_sentinel")
        env = build_child_env(policy)
        assert "ghp_unique_sentinel" not in "".join(env.values())

    def test_unknown_future_credential_is_caught_by_pattern(self, policy, monkeypatch):
        monkeypatch.setenv("SOME_NEW_PROVIDER_API_KEY", "x")
        assert "SOME_NEW_PROVIDER_API_KEY" not in build_child_env(policy)


class TestPreserved:
    def test_shell_essentials_survive(self, policy):
        env = build_child_env(policy)
        assert env.get("PATH") and env.get("HOME")

    def test_ordinary_vars_survive(self, policy, monkeypatch):
        monkeypatch.setenv("MY_PROJECT_MODE", "test")
        assert build_child_env(policy)["MY_PROJECT_MODE"] == "test"

    def test_locale_is_set_for_deterministic_sorting(self, policy):
        """`sort`/`uniq` output changes with locale; pinning it keeps tool
        results reproducible across machines."""
        assert build_child_env(policy).get("LC_ALL")


class TestOptIn:
    def test_allow_credentials_passes_them_through(self, policy_factory, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        env = build_child_env(policy_factory(allow_credentials=True))
        assert env["GITHUB_TOKEN"] == "ghp_x"

    def test_default_policy_does_not(self, policy, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        assert "GITHUB_TOKEN" not in build_child_env(policy)


@pytest.fixture
def policy_factory(tmp_path):
    def make(**kw):
        return SandboxPolicy(root=tmp_path, **kw)
    return make
