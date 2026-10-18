"""Tests for harness validators."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from supervisor.harness.core.errors import (
    InvalidEnvironmentValue,
    PathTraversalAttempt,
)
from supervisor.harness.storage.validators import (
    get_env_url,
    sanitize_env_value,
    validate_path_safe,
    validate_session_dir,
)


class TestValidatePathSafe:
    """Tests for validate_path_safe function."""

    def test_valid_path(self, tmp_path: Path) -> None:
        """Test valid path within base passes."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        result = validate_path_safe(subdir, tmp_path)
        assert result == subdir.resolve()

    def test_path_traversal_raises(self, tmp_path: Path) -> None:
        """Test path traversal attempt raises."""
        evil_path = tmp_path / ".." / "etc" / "passwd"
        with pytest.raises(PathTraversalAttempt):
            validate_path_safe(evil_path, tmp_path)

    def test_absolute_path_outside_base_raises(self, tmp_path: Path) -> None:
        """Test absolute path outside base raises."""
        with pytest.raises(PathTraversalAttempt):
            validate_path_safe("/etc/passwd", tmp_path)


class TestValidateSessionDir:
    """Tests for validate_session_dir function."""

    def test_valid_session_dir(self) -> None:
        """Test valid session directory passes."""
        result = validate_session_dir("/home/user/sessions/sess_123")
        assert result == Path("/home/user/sessions/sess_123")

    def test_traversal_in_path_raises(self) -> None:
        """Test path with .. raises."""
        with pytest.raises(PathTraversalAttempt):
            validate_session_dir("/home/user/../other/sess_123")

    def test_double_dot_component_raises(self) -> None:
        """Test .. component raises."""
        with pytest.raises(PathTraversalAttempt):
            validate_session_dir("sessions/../../../etc")


class TestSanitizeEnvValue:
    """Tests for sanitize_env_value function."""

    def test_valid_value(self) -> None:
        """Test valid value passes."""
        result = sanitize_env_value("KEY", "normal_value")
        assert result == "normal_value"

    def test_strips_whitespace(self) -> None:
        """Test whitespace is stripped."""
        result = sanitize_env_value("KEY", "  value  ")
        assert result == "value"

    def test_control_char_raises(self) -> None:
        """Test control character raises."""
        with pytest.raises(InvalidEnvironmentValue) as exc_info:
            sanitize_env_value("KEY", "value\x00with\x1fnull")
        assert "control" in str(exc_info.value)

    def test_newline_raises(self) -> None:
        """Test newline character raises."""
        with pytest.raises(InvalidEnvironmentValue):
            sanitize_env_value("KEY", "line1\nline2")


class TestGetEnvUrl:
    """Tests for get_env_url function."""

    def test_uses_default_when_not_set(self) -> None:
        """Test default is used when env var not set."""
        # Ensure env var is not set
        os.environ.pop("TEST_URL_NOT_SET", None)
        result = get_env_url("TEST_URL_NOT_SET", "http://default.com")
        assert result == "http://default.com"

    def test_strips_trailing_slash(self) -> None:
        """Test trailing slash is removed."""
        os.environ["TEST_URL_SLASH"] = "http://example.com/"
        try:
            result = get_env_url("TEST_URL_SLASH", "http://default.com")
            assert result == "http://example.com"
        finally:
            os.environ.pop("TEST_URL_SLASH", None)

    def test_uses_env_value(self) -> None:
        """Test environment value is used when set."""
        os.environ["TEST_URL_SET"] = "http://custom.com"
        try:
            result = get_env_url("TEST_URL_SET", "http://default.com")
            assert result == "http://custom.com"
        finally:
            os.environ.pop("TEST_URL_SET", None)

    def test_empty_env_uses_default(self) -> None:
        """Test empty env var uses default."""
        os.environ["TEST_URL_EMPTY"] = ""
        try:
            result = get_env_url("TEST_URL_EMPTY", "http://default.com")
            assert result == "http://default.com"
        finally:
            os.environ.pop("TEST_URL_EMPTY", None)
