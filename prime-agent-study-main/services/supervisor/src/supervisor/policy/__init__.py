"""Policy subsystem — comprehensive policy enforcement for supervisor.

This module provides all policy-related functionality:

Subsystems:
    approvals - Human-in-the-loop approval workflow
    budget - Cost and token budget tracking
    guards - Loop and stall detection
    rate_limit - Request rate limiting
    quotas - Tenant resource quotas
    command - Command denylist/allowlist
    audit - Policy decision audit logging

Core Types:
    types - All shared types, enums, dataclasses
    errors - Exception hierarchy
    protocols - Abstract interfaces for DI

Standalone:
    sandbox_policy - Sandbox file effects and approvals
    leader_lease - Distributed coordination

Example:
    from supervisor.policy import (
        ApprovalBroker,
        BudgetTracker,
        LoopGuard,
        CommandPolicy,
        QuotaEnforcer,
    )

    # Initialize components
    approvals = ApprovalBroker(session_factory)
    budget = BudgetTracker(session_factory)
    guard = LoopGuard()
    commands = CommandPolicy()
    quotas = QuotaEnforcer(session_factory)
"""

# =============================================================================
# Types
# =============================================================================
# =============================================================================
# Approvals
# =============================================================================
from supervisor.policy.approvals import ApprovalBroker

# =============================================================================
# Audit
# =============================================================================
from supervisor.policy.audit import DEFAULT_BATCH_SIZE, DEFAULT_FLUSH_INTERVAL_MS

# =============================================================================
# Budget
# =============================================================================
from supervisor.policy.budget import BudgetTracker, ModelPrice, ModelPricing

# =============================================================================
# Command Policy
# =============================================================================
from supervisor.policy.command import (
    DEFAULT_FLOOR_RULES,
    CommandPolicy,
    CommandScanner,
    create_rule,
)

# =============================================================================
# Errors
# =============================================================================
from supervisor.policy.errors import (
    ApprovalAlreadyResponded,
    ApprovalError,
    ApprovalNotFound,
    ApprovalTimedOut,
    BudgetExceeded,
    CommandDenied,
    CommandRequiresApproval,
    CostQuotaExceeded,
    InvalidApprovalChoice,
    PolicyError,
    QuotaExceeded,
    SandboxQuotaExceeded,
    SessionQuotaExceeded,
)

# =============================================================================
# Guards
# =============================================================================
from supervisor.policy.guards import (
    DEFAULT_HEARTBEAT_INTERVAL_MS,
    DEFAULT_QUIET_THRESHOLD_MS,
    DEFAULT_RUN_STALL_BUDGET_MS,
    DEFAULT_STALL_ROUND_LIMIT,
    EnhancedStallGuard,
    LoopGuard,
    StallGuard,
    call_hash,
    canonicalize_args,
)

# =============================================================================
# Leader Lease
# =============================================================================
from supervisor.policy.leader_lease import (
    APPROVAL_SWEEPER_LOCK_KEY,
    SCHEDULED_TASK_LOCK_KEY,
    STALE_SESSION_LOCK_KEY,
)

# =============================================================================
# Protocols
# =============================================================================
from supervisor.policy.protocols import (
    ApprovalBrokerProtocol,
    ApprovalGrantStoreProtocol,
    ApprovalRecoveryProtocol,
    AuditLoggerProtocol,
    BudgetTrackerProtocol,
    CommandPolicyProtocol,
    CostEstimatorProtocol,
    LoopGuardProtocol,
    QuotaEnforcerProtocol,
    RateLimiterProtocol,
    StallGuardProtocol,
)

# =============================================================================
# Quotas
# =============================================================================
from supervisor.policy.quotas import DEFAULT_QUOTA_LIMITS, QuotaEnforcer

# =============================================================================
# Rate Limiting
# =============================================================================
from supervisor.policy.rate_limit import MemoryRateLimiter

# =============================================================================
# Sandbox Policy
# =============================================================================
from supervisor.policy.sandbox_policy import (
    TOOL_CLASSES,
    SandboxPolicy,
    SandboxPolicyConfig,
    ToolClass,
)

# =============================================================================
# Security Screening
# =============================================================================
from supervisor.policy.security import (
    DANGEROUS_PATTERNS,
    DEFAULT_MAX_QUARANTINE_SIZE,
    DEFAULT_QUARANTINE_RETENTION_MS,
    DEFAULT_THREAT_THRESHOLD,
    SUSPICIOUS_PATTERNS,
    ClassifierType,
    ContentType,
    PatternMatch,
    QuarantinedContent,
    SecurityVerdict,
    ThreatLevel,
)
from supervisor.policy.types import (
    # Constants
    ALLOW_CHOICES,
    DEFAULT_APPROVAL_TIMEOUT_S,
    DEFAULT_RATE_WINDOW_MS,
    DEFAULT_STALL_ITERATIONS,
    Approval,
    ApprovalContext,
    ApprovalDefault,
    ApprovalGrant,
    ApprovalScope,
    # Enums
    ApprovalStatus,
    AuditAction,
    AuditEvent,
    # Dataclasses
    BudgetSpec,
    BudgetState,
    CommandAction,
    CommandDecision,
    CommandPolicyMode,
    CommandRule,
    FileEffects,
    GuardAction,
    GuardConfig,
    GuardDetector,
    GuardVerdict,
    QuotaLimits,
    QuotaState,
    RateDecision,
    RateDecisionStatus,
    StallSignal,
    StallState,
)

__all__ = [
    "ALLOW_CHOICES",
    "APPROVAL_SWEEPER_LOCK_KEY",
    "DANGEROUS_PATTERNS",
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FLOOR_RULES",
    "DEFAULT_FLUSH_INTERVAL_MS",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_MAX_QUARANTINE_SIZE",
    "DEFAULT_QUARANTINE_RETENTION_MS",
    "DEFAULT_QUIET_THRESHOLD_MS",
    "DEFAULT_QUOTA_LIMITS",
    "DEFAULT_RATE_WINDOW_MS",
    "DEFAULT_RUN_STALL_BUDGET_MS",
    "DEFAULT_STALL_ITERATIONS",
    "DEFAULT_STALL_ROUND_LIMIT",
    "DEFAULT_THREAT_THRESHOLD",
    "SCHEDULED_TASK_LOCK_KEY",
    "STALE_SESSION_LOCK_KEY",
    "SUSPICIOUS_PATTERNS",
    "TOOL_CLASSES",
    "Approval",
    "ApprovalAlreadyResponded",
    "ApprovalBroker",
    "ApprovalBrokerProtocol",
    "ApprovalContext",
    "ApprovalDefault",
    "ApprovalError",
    "ApprovalGrant",
    "ApprovalGrantStoreProtocol",
    "ApprovalNotFound",
    "ApprovalRecoveryProtocol",
    "ApprovalScope",
    "ApprovalStatus",
    "ApprovalTimedOut",
    "AuditAction",
    "AuditEvent",
    "AuditLoggerProtocol",
    "BudgetExceeded",
    "BudgetSpec",
    "BudgetState",
    "BudgetTracker",
    "BudgetTrackerProtocol",
    "ClassifierType",
    "CommandAction",
    "CommandDecision",
    "CommandDenied",
    "CommandPolicy",
    "CommandPolicyMode",
    "CommandPolicyProtocol",
    "CommandRequiresApproval",
    "CommandRule",
    "CommandScanner",
    "ContentType",
    "CostEstimatorProtocol",
    "CostQuotaExceeded",
    "EnhancedStallGuard",
    "FileEffects",
    "GuardAction",
    "GuardConfig",
    "GuardDetector",
    "GuardVerdict",
    "InvalidApprovalChoice",
    "LoopGuard",
    "LoopGuardProtocol",
    "MemoryRateLimiter",
    "ModelPrice",
    "ModelPricing",
    "PatternMatch",
    "PolicyError",
    "QuarantinedContent",
    "QuotaEnforcer",
    "QuotaEnforcerProtocol",
    "QuotaExceeded",
    "QuotaLimits",
    "QuotaState",
    "RateDecision",
    "RateDecisionStatus",
    "RateLimiterProtocol",
    "SandboxPolicy",
    "SandboxPolicyConfig",
    "SandboxQuotaExceeded",
    "SecurityVerdict",
    "SessionQuotaExceeded",
    "StallGuard",
    "StallGuardProtocol",
    "StallSignal",
    "StallState",
    "ThreatLevel",
    "ToolClass",
    "call_hash",
    "canonicalize_args",
    "create_rule",
]
