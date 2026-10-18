"""Harness manifest parse + validate."""

from __future__ import annotations

import pytest

from supervisor.harness import (
    HarnessManifest,
    ManifestError,
    load_manifest,
    validate_and_freeze,
)


VALID = """
harness_version: coding-v1
protocol_version: 1
runtime: echo
requires_sandbox: true
image_digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
mode: sticky
tools:
  read: { class: pure }
recovery:
  default: fail
  by_tool_class:
    pure: auto_resume
"""


def test_valid_manifest_parses() -> None:
    m = load_manifest(VALID)
    assert isinstance(m, HarnessManifest)
    assert m.harness_version == "coding-v1"
    assert m.protocol_version == 1
    assert m.requires_sandbox is True
    assert m.tools["read"].tool_class.value == "pure"


def test_requires_sandbox_without_digest_rejected() -> None:
    raw = """
harness_version: x
protocol_version: 1
runtime: echo
requires_sandbox: true
mode: sticky
recovery:
  default: fail
"""
    with pytest.raises(ManifestError, match="image_digest"):
        load_manifest(raw)


def test_image_tag_rejected() -> None:
    raw = """
harness_version: x
protocol_version: 1
runtime: echo
requires_sandbox: true
image_digest: agent-harness:coding-v1
mode: sticky
recovery:
  default: fail
"""
    with pytest.raises(ManifestError, match="digest"):
        load_manifest(raw)


def test_stateless_with_sandbox_rejected() -> None:
    raw = """
harness_version: x
protocol_version: 1
runtime: echo
requires_sandbox: true
image_digest: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
mode: stateless
recovery:
  default: fail
"""
    with pytest.raises(ManifestError, match="stateless"):
        load_manifest(raw)


def test_unknown_tool_class_in_by_tool_class_rejected() -> None:
    raw = """
harness_version: x
protocol_version: 1
runtime: echo
requires_sandbox: false
mode: stateless
recovery:
  default: fail
  by_tool_class:
    pureish: auto_resume
"""
    with pytest.raises(ManifestError, match="unknown tool class"):
        load_manifest(raw)


def test_missing_recovery_default_rejected() -> None:
    raw = """
harness_version: x
protocol_version: 1
runtime: echo
requires_sandbox: false
mode: stateless
recovery:
  by_tool_class:
    pure: auto_resume
"""
    with pytest.raises(ManifestError, match="recovery.default"):
        load_manifest(raw)


def test_unknown_top_level_key_ignored() -> None:
    raw = VALID + "\nfuture_field: yes\n"
    m = load_manifest(raw)
    assert m.harness_version == "coding-v1"


def test_manifest_immutable() -> None:
    m = load_manifest(VALID)
    with pytest.raises(Exception):
        m.harness_version = "nope"  # type: ignore[misc]


def test_verify_then_resume_alias() -> None:
    data = {
        "harness_version": "x",
        "protocol_version": 1,
        "runtime": "echo",
        "requires_sandbox": False,
        "mode": "stateless",
        "recovery": {
            "default": "fail",
            "by_tool_class": {"transactional": "verify_then_resume"},
        },
    }
    m = validate_and_freeze(data)
    from supervisor.harness import RecoveryAction, ToolClass

    assert (
        m.recovery.by_tool_class[ToolClass.TRANSACTIONAL]
        == RecoveryAction.NEEDS_REVIEW
    )
