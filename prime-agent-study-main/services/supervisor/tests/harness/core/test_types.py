"""Tests for harness types."""

from __future__ import annotations

import pytest

from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
)
from supervisor.harness.core.types import (
    ApprovalSpec,
    BudgetSpec,
    GuardSpec,
    HarnessManifest,
    RecoveryPolicy,
    RuntimeCapabilities,
    SubagentsSpec,
    ToolSpec,
    default_recovery_policy,
)


class TestToolSpec:
    """Tests for ToolSpec dataclass."""

    def test_creation(self) -> None:
        """Test creating a ToolSpec."""
        spec = ToolSpec(tool_class=ToolClass.PURE)
        assert spec.tool_class == ToolClass.PURE

    def test_frozen(self) -> None:
        """Test ToolSpec is immutable."""
        spec = ToolSpec(tool_class=ToolClass.PURE)
        with pytest.raises(AttributeError):
            spec.tool_class = ToolClass.IDEMPOTENT  # type: ignore

    def test_body_for_digest(self) -> None:
        """Test body_for_digest returns correct structure."""
        spec = ToolSpec(tool_class=ToolClass.IDEMPOTENT)
        body = spec.body_for_digest()
        assert body == {"tool_class": "idempotent"}


class TestBudgetSpec:
    """Tests for BudgetSpec dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        spec = BudgetSpec()
        assert spec.max_cost_per_session is None
        assert spec.max_tokens_per_turn is None

    def test_with_values(self) -> None:
        """Test with explicit values."""
        spec = BudgetSpec(max_cost_per_session=10.0, max_tokens_per_turn=1000)
        assert spec.max_cost_per_session == 10.0
        assert spec.max_tokens_per_turn == 1000


class TestGuardSpec:
    """Tests for GuardSpec dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        spec = GuardSpec()
        assert spec.repetition == GuardBehavior.ABORT
        assert spec.oscillation == GuardBehavior.ABORT
        assert spec.stall_iterations == 10

    def test_body_for_digest(self) -> None:
        """Test body_for_digest."""
        spec = GuardSpec(repetition=GuardBehavior.WARN)
        body = spec.body_for_digest()
        assert body["repetition"] == "warn"
        assert body["oscillation"] == "abort"


class TestApprovalSpec:
    """Tests for ApprovalSpec dataclass."""

    def test_defaults(self) -> None:
        """Test default values."""
        spec = ApprovalSpec()
        assert spec.timeout_s == 300.0
        assert spec.on_timeout == ApprovalOnTimeout.DENY
        assert spec.required is False
        assert spec.require_approval == ()

    def test_with_tools(self) -> None:
        """Test with require_approval list."""
        spec = ApprovalSpec(require_approval=("bash", "npm"))
        assert spec.require_approval == ("bash", "npm")


class TestRecoveryPolicy:
    """Tests for RecoveryPolicy dataclass."""

    def test_classify(self) -> None:
        """Test tool classification."""
        policy = RecoveryPolicy(
            default=RecoveryAction.FAIL,
            tool_classes={"read": ToolClass.PURE, "write": ToolClass.IDEMPOTENT},
        )
        assert policy.classify("read") == ToolClass.PURE
        assert policy.classify("write") == ToolClass.IDEMPOTENT
        assert policy.classify("unknown") is None

    def test_action_for(self) -> None:
        """Test action lookup."""
        policy = RecoveryPolicy(
            default=RecoveryAction.FAIL,
            by_tool_class={ToolClass.PURE: RecoveryAction.AUTO_RESUME},
            tool_classes={"read": ToolClass.PURE},
        )
        assert policy.action_for("read") == RecoveryAction.AUTO_RESUME
        assert policy.action_for("unknown") == RecoveryAction.FAIL


class TestDefaultRecoveryPolicy:
    """Tests for default_recovery_policy function."""

    def test_default_policy(self) -> None:
        """Test default policy has expected classifications."""
        policy = default_recovery_policy()
        assert policy.classify("read") == ToolClass.PURE
        assert policy.classify("bash") == ToolClass.NON_ATOMIC
        assert policy.action_for("read") == RecoveryAction.AUTO_RESUME
        assert policy.action_for("bash") == RecoveryAction.FAIL


class TestHarnessManifest:
    """Tests for HarnessManifest dataclass."""

    def test_creation(self) -> None:
        """Test creating a manifest."""
        manifest = HarnessManifest(
            harness_version="v1.0.0",
            protocol_version=1,
            runtime="echo",
            requires_sandbox=False,
            mode=RuntimeMode.STATELESS,
            image_digest=None,
            extension_bundle_uri=None,
            tools={},
            recovery=RecoveryPolicy(default=RecoveryAction.FAIL),
        )
        assert manifest.harness_version == "v1.0.0"
        assert manifest.version == "v1.0.0"  # alias

    def test_max_subagent_depth_default(self) -> None:
        """Test max_subagent_depth defaults to 3."""
        manifest = HarnessManifest(
            harness_version="v1",
            protocol_version=1,
            runtime="echo",
            requires_sandbox=False,
            mode=RuntimeMode.STATELESS,
            image_digest=None,
            extension_bundle_uri=None,
            tools={},
            recovery=RecoveryPolicy(default=RecoveryAction.FAIL),
        )
        assert manifest.max_subagent_depth == 3

    def test_max_subagent_depth_custom(self) -> None:
        """Test max_subagent_depth with custom value."""
        manifest = HarnessManifest(
            harness_version="v1",
            protocol_version=1,
            runtime="echo",
            requires_sandbox=False,
            mode=RuntimeMode.STATELESS,
            image_digest=None,
            extension_bundle_uri=None,
            tools={},
            recovery=RecoveryPolicy(default=RecoveryAction.FAIL),
            subagents=SubagentsSpec(max_depth=5),
        )
        assert manifest.max_subagent_depth == 5


class TestRuntimeCapabilities:
    """Tests for RuntimeCapabilities dataclass."""

    def test_creation(self) -> None:
        """Test creating capabilities."""
        caps = RuntimeCapabilities(
            protocol_versions=frozenset({1, 2}),
            commands=frozenset({"prompt", "abort"}),
            supports_approvals=True,
        )
        assert 1 in caps.protocol_versions
        assert "prompt" in caps.commands
        assert caps.supports_approvals is True

