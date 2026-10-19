"""Tests for harness errors."""

from __future__ import annotations

import pytest

from supervisor.harness.core.errors import (
    ApprovalsNotSupported,
    HarnessError,
    HarnessNotOffered,
    IncompatibleHarness,
    InvalidDigest,
    InvalidEnvironmentValue,
    InvalidGatewayConfig,
    ManifestError,
    ManifestParseError,
    ManifestValidationError,
    MissingHarnessCli,
    MissingRuntimeCapability,
    PathTraversalAttempt,
    SandboxRequired,
    SpawnError,
    UnknownHarness,
    UnsupportedProtocol,
    VersionResolutionError,
)


class TestHarnessError:
    """Tests for base HarnessError."""

    def test_default_attributes(self) -> None:
        """Test default error attributes."""
        err = HarnessError("test message")
        assert str(err) == "test message"
        assert err.error_code == "HARNESS_ERROR"
        assert err.retryable is False
        assert err.detail == {}

    def test_with_detail(self) -> None:
        """Test error with detail dict."""
        err = HarnessError("test", detail={"key": "value"})
        assert err.detail == {"key": "value"}

    def test_to_dict(self) -> None:
        """Test to_dict serialization."""
        err = HarnessError("test message", detail={"foo": "bar"})
        d = err.to_dict()
        assert d["error_code"] == "HARNESS_ERROR"
        assert d["message"] == "test message"
        assert d["retryable"] is False
        assert d["detail"] == {"foo": "bar"}


class TestUnknownHarness:
    """Tests for UnknownHarness error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = UnknownHarness("v1.0.0")
        assert err.harness_version == "v1.0.0"
        assert err.error_code == "UNKNOWN_HARNESS"
        assert "v1.0.0" in str(err)

    def test_detail(self) -> None:
        """Test detail contains version."""
        err = UnknownHarness("v1.0.0")
        assert err.detail["harness_version"] == "v1.0.0"


class TestHarnessNotOffered:
    """Tests for HarnessNotOffered error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = HarnessNotOffered(harness_version="v1.0.0", status="deprecated")
        assert err.harness_version == "v1.0.0"
        assert err.status == "deprecated"
        assert err.error_code == "HARNESS_NOT_OFFERED"


class TestManifestValidationError:
    """Tests for ManifestValidationError."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = ManifestValidationError("runtime", "is required")
        assert err.field == "runtime"
        assert err.reason == "is required"
        assert err.error_code == "MANIFEST_VALIDATION_ERROR"
        assert "runtime" in str(err)


class TestInvalidDigest:
    """Tests for InvalidDigest error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = InvalidDigest("sha256:abc", "too short")
        assert err.digest == "sha256:abc"
        assert err.reason == "too short"


class TestUnsupportedProtocol:
    """Tests for UnsupportedProtocol error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = UnsupportedProtocol(requested=3, supported=[1, 2])
        assert err.requested == 3
        assert err.supported == [1, 2]
        assert "3" in str(err)


class TestMissingRuntimeCapability:
    """Tests for MissingRuntimeCapability error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = MissingRuntimeCapability(runtime="echo", missing=["fork", "resume"])
        assert err.runtime == "echo"
        assert err.missing == ["fork", "resume"]


class TestSandboxRequired:
    """Tests for SandboxRequired error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = SandboxRequired("v1.0.0")
        assert err.harness_version == "v1.0.0"
        assert "sandbox" in str(err).lower()


class TestMissingHarnessCli:
    """Tests for MissingHarnessCli error."""

    def test_message(self) -> None:
        """Test error message."""
        err = MissingHarnessCli()
        assert "HARNESS_CLI" in str(err)


class TestPathTraversalAttempt:
    """Tests for PathTraversalAttempt error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = PathTraversalAttempt("../../../etc/passwd")
        assert err.path == "../../../etc/passwd"
        assert "traversal" in str(err).lower()


class TestInvalidEnvironmentValue:
    """Tests for InvalidEnvironmentValue error."""

    def test_attributes(self) -> None:
        """Test error attributes."""
        err = InvalidEnvironmentValue("API_KEY", "contains control characters")
        assert err.key == "API_KEY"
        assert err.reason == "contains control characters"


class TestRetryableErrors:
    """Tests for retryable error flags."""

    def test_version_resolution_error_is_retryable(self) -> None:
        """Test VersionResolutionError is retryable."""
        err = VersionResolutionError("v1.0.0", "timeout")
        assert err.retryable is True

    def test_unknown_harness_not_retryable(self) -> None:
        """Test UnknownHarness is not retryable."""
        err = UnknownHarness("v1.0.0")
        assert err.retryable is False


class TestErrorInheritance:
    """Tests for error inheritance hierarchy."""

    def test_manifest_errors_inherit_from_harness_error(self) -> None:
        """Test ManifestError hierarchy."""
        err = ManifestValidationError("field", "reason")
        assert isinstance(err, ManifestError)
        assert isinstance(err, HarnessError)
        assert isinstance(err, Exception)

    def test_compatibility_errors_inherit_correctly(self) -> None:
        """Test compatibility error hierarchy."""
        err = UnsupportedProtocol(requested=3, supported=[1, 2])
        assert isinstance(err, IncompatibleHarness)
        assert isinstance(err, HarnessError)

    def test_spawn_errors_inherit_correctly(self) -> None:
        """Test spawn error hierarchy."""
        err = MissingHarnessCli()
        assert isinstance(err, SpawnError)
        assert isinstance(err, HarnessError)
