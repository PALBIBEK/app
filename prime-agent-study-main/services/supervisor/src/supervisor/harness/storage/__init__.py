"""Storage layer for harness - converters and validators.

This module provides:
- Bidirectional dict <-> dataclass transformations
- Security validators for paths and environment values
"""

from __future__ import annotations

from supervisor.harness.storage.converters import (
    approval_spec_from_dict,
    approval_spec_to_dict,
    budget_spec_from_dict,
    budget_spec_to_dict,
    guard_spec_from_dict,
    guard_spec_to_dict,
    load_manifest,
    manifest_from_dict,
    manifest_to_dict,
    parse_yaml,
    recovery_policy_from_dict,
    recovery_policy_to_dict,
    subagents_spec_from_dict,
    subagents_spec_to_dict,
    tool_spec_from_dict,
    tool_spec_to_dict,
    validate_and_freeze,
)
from supervisor.harness.storage.validators import (
    get_env_url,
    sanitize_env_value,
    validate_path_safe,
    validate_session_dir,
)

__all__ = [
    # Converters - from dict
    "approval_spec_from_dict",
    # Converters - to dict
    "approval_spec_to_dict",
    "budget_spec_from_dict",
    "budget_spec_to_dict",
    # Validators
    "get_env_url",
    "guard_spec_from_dict",
    "guard_spec_to_dict",
    # Parsing
    "load_manifest",
    "manifest_from_dict",
    "manifest_to_dict",
    "parse_yaml",
    "recovery_policy_from_dict",
    "recovery_policy_to_dict",
    "sanitize_env_value",
    "subagents_spec_from_dict",
    "subagents_spec_to_dict",
    "tool_spec_from_dict",
    "tool_spec_to_dict",
    "validate_and_freeze",
    "validate_path_safe",
    "validate_session_dir",
]
