"""Tests for harness digests."""

from __future__ import annotations

import pytest

from supervisor.harness.core.digests import (
    DIGEST_PATTERN,
    canonical_json_bytes,
    content_digest,
    validate_digest_format,
    verify_digest,
)
from supervisor.harness.core.errors import InvalidDigest


class TestDigestPattern:
    """Tests for DIGEST_PATTERN regex."""


    def test_invalid_digest_no_prefix(self) -> None:
        """Test digest without prefix doesn't match."""
        invalid = "a" * 64
        assert not DIGEST_PATTERN.match(invalid)

    def test_invalid_digest_wrong_length(self) -> None:
        """Test digest with wrong length doesn't match."""
        invalid = "sha256:" + "a" * 32
        assert not DIGEST_PATTERN.match(invalid)

    def test_invalid_digest_uppercase(self) -> None:
        """Test uppercase hex is valid (case insensitive)."""
        valid = "sha256:" + "A" * 64
        assert DIGEST_PATTERN.match(valid)


class TestCanonicalJsonBytes:
    """Tests for canonical_json_bytes function."""

    def test_sorted_keys(self) -> None:
        """Test keys are sorted."""
        obj = {"z": 1, "a": 2, "m": 3}
        result = canonical_json_bytes(obj)
        assert result == b'{"a":2,"m":3,"z":1}'

    def test_no_whitespace(self) -> None:
        """Test no insignificant whitespace."""
        obj = {"key": "value"}
        result = canonical_json_bytes(obj)
        assert b" " not in result

    def test_unicode_preserved(self) -> None:
        """Test unicode characters are preserved."""
        obj = {"emoji": "\u2764"}
        result = canonical_json_bytes(obj)
        assert "\u2764".encode("utf-8") in result

    def test_deterministic(self) -> None:
        """Test same input produces same output."""
        obj = {"a": 1, "b": [1, 2, 3]}
        result1 = canonical_json_bytes(obj)
        result2 = canonical_json_bytes(obj)
        assert result1 == result2


class TestContentDigest:
    """Tests for content_digest function."""

    def test_returns_sha256_prefix(self) -> None:
        """Test digest has sha256: prefix."""
        digest = content_digest({"test": "value"})
        assert digest.startswith("sha256:")

    def test_returns_64_hex_chars(self) -> None:
        """Test digest has 64 hex chars after prefix."""
        digest = content_digest({"test": "value"})
        hex_part = digest.split(":")[1]
        assert len(hex_part) == 64
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_deterministic(self) -> None:
        """Test same input produces same digest."""
        obj = {"key": "value", "nested": {"a": 1}}
        digest1 = content_digest(obj)
        digest2 = content_digest(obj)
        assert digest1 == digest2

    def test_different_input_different_digest(self) -> None:
        """Test different input produces different digest."""
        digest1 = content_digest({"a": 1})
        digest2 = content_digest({"a": 2})
        assert digest1 != digest2


class TestVerifyDigest:
    """Tests for verify_digest function."""

    def test_matching_digest_passes(self) -> None:
        """Test matching digest doesn't raise."""
        obj = {"test": "value"}
        expected = content_digest(obj)
        verify_digest(obj, expected)  # Should not raise

    def test_mismatched_digest_raises(self) -> None:
        """Test mismatched digest raises InvalidDigest."""
        obj = {"test": "value"}
        wrong_digest = "sha256:" + "0" * 64
        with pytest.raises(InvalidDigest) as exc_info:
            verify_digest(obj, wrong_digest)
        assert "computed=" in str(exc_info.value)


class TestValidateDigestFormat:
    """Tests for validate_digest_format function."""

    def test_valid_digest(self) -> None:
        """Test valid digest passes."""
        valid = "sha256:" + "a" * 64
        validate_digest_format(valid)  # Should not raise

    def test_missing_prefix_raises(self) -> None:
        """Test missing prefix raises InvalidDigest."""
        invalid = "a" * 64
        with pytest.raises(InvalidDigest) as exc_info:
            validate_digest_format(invalid)
        assert "prefix" in str(exc_info.value)

    def test_wrong_algorithm_raises(self) -> None:
        """Test wrong algorithm raises InvalidDigest."""
        invalid = "md5:" + "a" * 32
        with pytest.raises(InvalidDigest) as exc_info:
            validate_digest_format(invalid)
        assert "sha256" in str(exc_info.value)

    def test_wrong_length_raises(self) -> None:
        """Test wrong length raises InvalidDigest."""
        invalid = "sha256:" + "a" * 32
        with pytest.raises(InvalidDigest) as exc_info:
            validate_digest_format(invalid)
        assert "64 hex" in str(exc_info.value)
