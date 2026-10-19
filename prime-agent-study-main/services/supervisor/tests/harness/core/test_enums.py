"""Tests for harness enums."""

from __future__ import annotations

import json

import pytest

from supervisor.harness.core.enums import (
    ApprovalOnTimeout,
    GuardBehavior,
    HarnessStatus,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
    RECOVERY_ACTION_ALIASES,
)


class TestHarnessStatus:
    """Tests for HarnessStatus enum."""

    def test_values(self) -> None:
        """Test enum values match expected strings."""
        assert HarnessStatus.DRAFT == "draft"
        assert HarnessStatus.ACTIVE == "active"
        assert HarnessStatus.DEPRECATED == "deprecated"

    def test_json_serializable(self) -> None:
        """Test enum values can be JSON serialized."""
        data = {"status": HarnessStatus.ACTIVE}
        serialized = json.dumps(data)
        assert '"active"' in serialized

    def test_from_string(self) -> None:
        """Test creating enum from string value."""
        assert HarnessStatus("active") == HarnessStatus.ACTIVE
        assert HarnessStatus("draft") == HarnessStatus.DRAFT


class TestRuntimeMode:
    """Tests for RuntimeMode enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert RuntimeMode.STICKY == "sticky"
        assert RuntimeMode.STATELESS == "stateless"

    def test_from_string(self) -> None:
        """Test creating enum from string."""
        assert RuntimeMode("sticky") == RuntimeMode.STICKY


class TestToolClass:
    """Tests for ToolClass enum."""

    def test_all_values_present(self) -> None:
        """Test all expected tool classes exist."""
        assert ToolClass.PURE == "pure"
        assert ToolClass.IDEMPOTENT == "idempotent"
        assert ToolClass.TRANSACTIONAL == "transactional"
        assert ToolClass.NON_ATOMIC == "non_atomic"

    def test_iteration(self) -> None:
        """Test we can iterate over all values."""
        values = list(ToolClass)
        assert len(values) == 4


class TestRecoveryAction:
    """Tests for RecoveryAction enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert RecoveryAction.FAIL == "fail"
        assert RecoveryAction.NEEDS_REVIEW == "needs_review"
        assert RecoveryAction.AUTO_RESUME == "auto_resume"


class TestRecoveryActionAliases:
    """Tests for recovery action aliases."""

    def test_aliases_map_correctly(self) -> None:
        """Test YAML aliases map to correct actions."""
        assert RECOVERY_ACTION_ALIASES["fail"] == RecoveryAction.FAIL
        assert RECOVERY_ACTION_ALIASES["needs_review"] == RecoveryAction.NEEDS_REVIEW
        assert RECOVERY_ACTION_ALIASES["verify_then_resume"] == RecoveryAction.NEEDS_REVIEW
        assert RECOVERY_ACTION_ALIASES["auto_resume"] == RecoveryAction.AUTO_RESUME


class TestGuardBehavior:
    """Tests for GuardBehavior enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert GuardBehavior.ABORT == "abort"
        assert GuardBehavior.WARN == "warn"
        assert GuardBehavior.OFF == "off"


class TestApprovalOnTimeout:
    """Tests for ApprovalOnTimeout enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert ApprovalOnTimeout.DENY == "deny"
        assert ApprovalOnTimeout.ALLOW == "allow"
