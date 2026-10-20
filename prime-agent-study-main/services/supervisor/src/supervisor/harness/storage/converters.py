"""Dict <-> dataclass transformations for harness types.

Bidirectional conversion functions following catalog pattern.
All functions are pure and stateless.

Reference: catalog/storage/converters.py pattern.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import yaml

from supervisor.harness.core.digests import assert_digest
from supervisor.harness.core.enums import (
    RECOVERY_ACTION_ALIASES,
    ApprovalOnTimeout,
    GuardBehavior,
    RecoveryAction,
    RuntimeMode,
    ToolClass,
)
from supervisor.harness.core.errors import (
    ManifestParseError,
    ManifestValidationError,
)
from supervisor.harness.core.types import (
    ApprovalSpec,
    BudgetSpec,
    GuardSpec,
    HarnessManifest,
    RecoveryPolicy,
    SubagentsSpec,
    ToolSpec,
)

__all__ = [
    # From dict
    "approval_spec_from_dict",
    # To dict
    "approval_spec_to_dict",
    "budget_spec_from_dict",
    "budget_spec_to_dict",
    "guard_spec_from_dict",
    "guard_spec_to_dict",
    # Parsing
    "load_manifest",
    "manifest_from_dict",
    "manifest_to_dict",
    "parse_yaml",
    "recovery_policy_from_dict",
    "recovery_policy_to_dict",
    "subagents_spec_from_dict",
    "subagents_spec_to_dict",
    "tool_spec_from_dict",
    "tool_spec_to_dict",
    "validate_and_freeze",
]


# =============================================================================
# To Dict Conversions
# =============================================================================


def tool_spec_to_dict(spec: ToolSpec) -> dict[str, str]:
    """Convert ToolSpec to dict."""
    return {"class": spec.tool_class.value}


def budget_spec_to_dict(spec: BudgetSpec) -> dict[str, Any]:
    """Convert BudgetSpec to dict."""
    return asdict(spec)


def guard_spec_to_dict(spec: GuardSpec) -> dict[str, Any]:
    """Convert GuardSpec to dict."""
    return {
        "repetition": spec.repetition.value,
        "oscillation": spec.oscillation.value,
        "stall_iterations": spec.stall_iterations,
    }


def approval_spec_to_dict(spec: ApprovalSpec) -> dict[str, Any]:
    """Convert ApprovalSpec to dict."""
    return {
        "timeout_s": spec.timeout_s,
        "on_timeout": spec.on_timeout.value,
        "required": spec.required,
        "require_approval": list(spec.require_approval),
    }


def subagents_spec_to_dict(spec: SubagentsSpec) -> dict[str, Any]:
    """Convert SubagentsSpec to dict."""
    return asdict(spec)


def recovery_policy_to_dict(policy: RecoveryPolicy) -> dict[str, Any]:
    """Convert RecoveryPolicy to dict."""
    return {
        "default": policy.default.value,
        "by_tool_class": {k.value: v.value for k, v in policy.by_tool_class.items()},
        "tool_classes": {k: v.value for k, v in policy.tool_classes.items()},
    }


def manifest_to_dict(manifest: HarnessManifest) -> dict[str, Any]:
    """JSON-serializable snapshot for harness_versions / sessions storage."""
    return {
        "harness_version": manifest.harness_version,
        "protocol_version": manifest.protocol_version,
        "runtime": manifest.runtime,
        "requires_sandbox": manifest.requires_sandbox,
        "mode": manifest.mode.value,
        "image_digest": manifest.image_digest,
        "extension_bundle_uri": manifest.extension_bundle_uri,
        "tools": {k: tool_spec_to_dict(v) for k, v in manifest.tools.items()},
        "recovery": recovery_policy_to_dict(manifest.recovery),
        "budget": budget_spec_to_dict(manifest.budget) if manifest.budget else None,
        "guards": guard_spec_to_dict(manifest.guards) if manifest.guards else None,
        "approvals": approval_spec_to_dict(manifest.approvals) if manifest.approvals else None,
        "subagents": subagents_spec_to_dict(manifest.subagents) if manifest.subagents else None,
        "model": manifest.model,
        "system_prompt_sha": manifest.system_prompt_sha,
    }


# =============================================================================
# From Dict Conversions
# =============================================================================


def tool_spec_from_dict(data: str | dict[str, Any]) -> ToolSpec:
    """Parse tool spec from dict or string."""
    if isinstance(data, str):
        cls_name = data
    elif isinstance(data, dict):
        cls_name = str(data.get("class") or data.get("tool_class") or "")
    else:
        raise ManifestValidationError("tool", "invalid spec format")

    try:
        tool_class = ToolClass(cls_name)
    except ValueError as exc:
        raise ManifestValidationError("tool", f"unknown class {cls_name!r}") from exc

    return ToolSpec(tool_class=tool_class)


def budget_spec_from_dict(data: dict[str, Any]) -> BudgetSpec:
    """Parse BudgetSpec from dict."""
    return BudgetSpec(
        max_cost_per_session=(
            float(data["max_cost_per_session"])
            if data.get("max_cost_per_session") is not None
            else None
        ),
        max_tokens_per_turn=(
            int(data["max_tokens_per_turn"])
            if data.get("max_tokens_per_turn") is not None
            else None
        ),
    )


def guard_spec_from_dict(data: dict[str, Any]) -> GuardSpec:
    """Parse GuardSpec from dict."""
    rep = str(data.get("repetition", "abort"))
    osc = str(data.get("oscillation", "abort"))

    for label, val in (("repetition", rep), ("oscillation", osc)):
        if val not in ("abort", "warn", "off"):
            raise ManifestValidationError(f"guards.{label}", "must be abort|warn|off")

    return GuardSpec(
        repetition=GuardBehavior(rep),
        oscillation=GuardBehavior(osc),
        stall_iterations=int(data.get("stall_iterations", 10)),
    )


def approval_spec_from_dict(data: dict[str, Any]) -> ApprovalSpec:
    """Parse ApprovalSpec from dict."""
    on_timeout = str(data.get("on_timeout", "deny"))
    if on_timeout not in ("deny", "allow"):
        raise ManifestValidationError("approvals.on_timeout", "must be deny|allow")

    require_raw = data.get("require_approval", ())
    if require_raw is None:
        require_names: tuple[str, ...] = ()
    elif isinstance(require_raw, (list, tuple)):
        require_names = tuple(str(n) for n in require_raw)
    else:
        raise ManifestValidationError(
            "approvals.require_approval",
            "must be a list of tool names",
        )

    return ApprovalSpec(
        timeout_s=float(data.get("timeout_s", 300)),
        on_timeout=ApprovalOnTimeout(on_timeout),
        required=bool(data.get("required", False)),
        require_approval=require_names,
    )


def subagents_spec_from_dict(data: dict[str, Any]) -> SubagentsSpec:
    """Parse SubagentsSpec from dict."""
    max_depth = int(data.get("max_depth", 3))
    if max_depth < 1:
        raise ManifestValidationError("subagents.max_depth", "must be >= 1")

    return SubagentsSpec(
        max_depth=max_depth,
        default_mode=str(data.get("default_mode", "one_shot")),
        allow_background=bool(data.get("allow_background", True)),
    )


def recovery_policy_from_dict(
    data: dict[str, Any],
    tools: dict[str, ToolSpec],
) -> RecoveryPolicy:
    """Parse RecoveryPolicy from dict."""
    if "default" not in data:
        raise ManifestValidationError("recovery.default", "is required")

    default = _parse_action(data["default"], where="recovery.default")

    by_tool_class: dict[ToolClass, RecoveryAction] = {}
    for key, val in (data.get("by_tool_class") or {}).items():
        try:
            tc = ToolClass(key)
        except ValueError as exc:
            raise ManifestValidationError(
                f"recovery.by_tool_class.{key}",
                f"unknown tool class {key!r}",
            ) from exc
        by_tool_class[tc] = _parse_action(val, where=f"recovery.by_tool_class.{key}")

    tool_classes: dict[str, ToolClass] = {n: t.tool_class for n, t in tools.items()}
    # Legacy: recovery.tool_classes map
    for name, cls_name in (data.get("tool_classes") or {}).items():
        try:
            tool_classes[str(name)] = ToolClass(cls_name)
        except ValueError as exc:
            raise ManifestValidationError(
                "recovery.tool_classes",
                f"unknown class {cls_name!r}",
            ) from exc

    return RecoveryPolicy(
        default=default,
        by_tool_class=by_tool_class,
        tool_classes=tool_classes,
    )


def _parse_action(raw: Any, *, where: str) -> RecoveryAction:
    """Parse recovery action from string with alias support."""
    try:
        return RECOVERY_ACTION_ALIASES[str(raw)]
    except KeyError as exc:
        raise ManifestValidationError(where, f"unknown action {raw!r}") from exc


def manifest_from_dict(data: dict[str, Any]) -> HarnessManifest:
    """Parse and validate manifest from dict."""
    return validate_and_freeze(data)


# =============================================================================
# YAML Parsing
# =============================================================================


def parse_yaml(raw: str | bytes) -> dict[str, Any]:
    """Parse YAML manifest to dict.

    Args:
        raw: Raw YAML content

    Returns:
        Parsed dict

    Raises:
        ManifestParseError: If YAML is invalid
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestParseError(str(exc)) from exc

    if not isinstance(data, dict):
        raise ManifestParseError("manifest root must be a mapping")

    return data


def load_manifest(raw: str | bytes) -> HarnessManifest:
    """Load and validate manifest from YAML.

    Args:
        raw: Raw YAML content

    Returns:
        Frozen HarnessManifest

    Raises:
        ManifestParseError: If YAML is invalid
        ManifestValidationError: If validation fails
    """
    return validate_and_freeze(parse_yaml(raw))


def validate_and_freeze(data: dict[str, Any]) -> HarnessManifest:
    """Validate manifest data and create frozen dataclass.

    Unknown top-level keys are ignored. Fails loudly on invalid values.

    Args:
        data: Parsed manifest dict

    Returns:
        Frozen HarnessManifest

    Raises:
        ManifestValidationError: If validation fails
    """
    # Import here to avoid circular import
    from supervisor.agent.factory import known_runtime_names

    # Version
    version = data.get("harness_version") or data.get("version")
    if not version or not isinstance(version, str):
        raise ManifestValidationError("harness_version", "is required")

    # Protocol version
    pv_raw = data.get("protocol_version")
    if pv_raw is None:
        raise ManifestValidationError("protocol_version", "is required")
    try:
        protocol_version = int(pv_raw)
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(
            "protocol_version",
            f"must be int, got {pv_raw!r}",
        ) from exc

    # Runtime
    runtime = data.get("runtime")
    if not runtime or not isinstance(runtime, str):
        raise ManifestValidationError("runtime", "is required")
    if runtime not in known_runtime_names():
        raise ManifestValidationError("runtime", f"{runtime!r} is not registered")

    # Requires sandbox
    if "requires_sandbox" not in data:
        raise ManifestValidationError("requires_sandbox", "is required")
    requires_sandbox = bool(data["requires_sandbox"])

    # Mode
    mode_raw = data.get("mode")
    if mode_raw is None:
        mode_raw = "sticky" if requires_sandbox else "stateless"
    if mode_raw not in ("sticky", "stateless"):
        raise ManifestValidationError("mode", f"must be sticky|stateless, got {mode_raw!r}")
    if mode_raw == "stateless" and requires_sandbox:
        raise ManifestValidationError("mode", "stateless must not require a sandbox")
    mode = RuntimeMode(mode_raw)

    # Image digest
    image_digest = data.get("image_digest")
    # Legacy key "image" only accepted if it is already a digest
    if image_digest is None and data.get("image"):
        image_digest = data.get("image")

    if requires_sandbox:
        if not image_digest:
            raise ManifestValidationError("image_digest", "required when requires_sandbox=true")
        assert_digest(str(image_digest))
        image_digest = str(image_digest)
    elif image_digest:
        image_digest = str(image_digest)
        if image_digest == "":
            image_digest = None
        else:
            assert_digest(image_digest)
    else:
        image_digest = None

    # Extension bundle
    bundle = data.get("extension_bundle_uri") or data.get("bundle_uri")
    extension_bundle_uri = str(bundle) if bundle else None

    # Tools
    tools = _parse_tools(data.get("tools") or {})

    # Recovery
    recovery_data = data.get("recovery")
    if recovery_data is None or not isinstance(recovery_data, dict):
        raise ManifestValidationError("recovery", "is required")
    recovery = recovery_policy_from_dict(recovery_data, tools)

    # Optional specs
    budget = budget_spec_from_dict(data["budget"]) if data.get("budget") else None
    guards = guard_spec_from_dict(data["guards"]) if data.get("guards") else None
    approvals = approval_spec_from_dict(data["approvals"]) if data.get("approvals") else None
    subagents = subagents_spec_from_dict(data["subagents"]) if data.get("subagents") else None

    return HarnessManifest(
        harness_version=version,
        protocol_version=protocol_version,
        runtime=runtime,
        requires_sandbox=requires_sandbox,
        mode=mode,
        image_digest=image_digest,
        extension_bundle_uri=extension_bundle_uri,
        tools=tools,
        recovery=recovery,
        budget=budget,
        guards=guards,
        approvals=approvals,
        subagents=subagents,
        model=str(data["model"]) if data.get("model") else None,
        system_prompt_sha=(
            str(data["system_prompt_sha"]) if data.get("system_prompt_sha") else None
        ),
    )


def _parse_tools(raw: Any) -> dict[str, ToolSpec]:
    """Parse tools mapping from manifest."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ManifestValidationError("tools", "must be a mapping")

    out: dict[str, ToolSpec] = {}
    for name, spec in raw.items():
        try:
            out[str(name)] = tool_spec_from_dict(spec)
        except ManifestValidationError as exc:
            raise ManifestValidationError(f"tools.{name}", exc.reason) from exc

    return out
