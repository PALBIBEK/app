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
from supervisor.policy.types import (
    # Enums
    ApprovalStatus,
    ApprovalScope,
    GuardAction,
    GuardDetector,
    StallSignal,
    FileEffects,
    ApprovalDefault,
    CommandAction,
    CommandPolicyMode,
    AuditAction,
    RateDecisionStatus,
    # Dataclasses
    BudgetSpec,
    BudgetState,
    GuardConfig,
    GuardVerdict,
    Approval,
    ApprovalGrant,
    ApprovalContext,
    CommandRule,
    CommandDecision,
    RateDecision,
    AuditEvent,
    QuotaLimits,
    QuotaState,
    StallState,
    # Constants
    ALLOW_CHOICES,
    DEFAULT_APPROVAL_TIMEOUT_S,
    DEFAULT_RATE_WINDOW_MS,
    DEFAULT_STALL_ITERATIONS,
)

# =============================================================================
# Errors
# =============================================================================
from supervisor.policy.errors import (
    # Base
    PolicyError,
    # Budget
    BudgetExceeded,
    BudgetCostExceeded,
    BudgetTokensExceeded,
    # Quota
    QuotaExceeded,
    SessionQuotaExceeded,
    SandboxQuotaExceeded,
    CostQuotaExceeded,
    # Approval
    ApprovalError,
    ApprovalNotFound,
    ApprovalAlreadyResponded,
    ApprovalTimedOut,
    InvalidApprovalChoice,
    ApprovalRecoveryFailed,
    # Command
    CommandDenied,
    CommandRequiresApproval,
    # Rate Limiting
    RateLimitExceeded,
    # Guard
    GuardTripped,
    LoopDetected,
    StallDetected,
    # Audit
    AuditWriteFailed,
)

# =============================================================================
# Protocols
# =============================================================================
from supervisor.policy.protocols import (
    BudgetTrackerProtocol,
    CostEstimatorProtocol,
    ApprovalBrokerProtocol,
    ApprovalGrantStoreProtocol,
    ApprovalRecoveryProtocol,
    LoopGuardProtocol,
    StallGuardProtocol,
    RateLimiterProtocol,
    QuotaEnforcerProtocol,
    CommandPolicyProtocol,
    AuditLoggerProtocol,
)

# =============================================================================
# Approvals
# =============================================================================
from supervisor.policy.approvals import (
    ApprovalBroker,
    ApprovalGrantStore,
    ApprovalRecovery,
    InMemoryApprovalRecovery,
    PostgresApprovalRecovery,
)

# =============================================================================
# Budget
# =============================================================================
from supervisor.policy.budget import (
    BudgetTracker,
    CostEstimator,
    ModelPricing,
    ModelPrice,
)

# =============================================================================
# Guards
# =============================================================================
from supervisor.policy.guards import (
    LoopGuard,
    StallGuard,
    EnhancedStallGuard,
    DEFAULT_RUN_STALL_BUDGET_MS,
    DEFAULT_STALL_ROUND_LIMIT,
    DEFAULT_HEARTBEAT_INTERVAL_MS,
    DEFAULT_QUIET_THRESHOLD_MS,
    call_hash,
    canonicalize_args,
)

# =============================================================================
# Rate Limiting
# =============================================================================
from supervisor.policy.rate_limit import (
    MemoryRateLimiter,
    PostgresRateLimiter,
)

# =============================================================================
# Quotas
# =============================================================================
from supervisor.policy.quotas import (
    QuotaEnforcer,
    DEFAULT_QUOTA_LIMITS,
    parse_quota_limits,
)

# =============================================================================
# Command Policy
# =============================================================================
from supervisor.policy.command import (
    CommandPolicy,
    CommandScanner,
    DEFAULT_FLOOR_RULES,
    create_rule,
)

# =============================================================================
# Audit
# =============================================================================
from supervisor.policy.audit import (
    AuditLogger,
    InMemoryAuditLogger,
    PostgresAuditLogger,
    DEFAULT_BATCH_SIZE,
    DEFAULT_FLUSH_INTERVAL_MS,
    create_event,
    format_event,
)

# =============================================================================
# Sandbox Policy
# =============================================================================
from supervisor.policy.sandbox_policy import (
    SandboxPolicy,
    SandboxPolicyConfig,
    ToolClass,
    TOOL_CLASSES,
)

# =============================================================================
# Leader Lease
# =============================================================================
from supervisor.policy.leader_lease import (
    LeaderLease,
    try_acquire_sweeper_lease,
    run_with_approval_sweeper_lease,
    APPROVAL_SWEEPER_LOCK_KEY,
    STALE_SESSION_LOCK_KEY,
    SCHEDULED_TASK_LOCK_KEY,
)

# =============================================================================
# Security Screening
# =============================================================================
from supervisor.policy.security import (
    # Main screener
    SecurityScreener,
    # Classifiers
    RegexClassifier,
    HeuristicClassifier,
    # Quarantine
    QuarantineStore,
    InMemoryQuarantineStore,
    # Types
    ThreatLevel,
    ClassifierType,
    ContentType,
    SecurityVerdict,
    QuarantinedContent,
    PatternMatch,
    # Constants
    DEFAULT_THREAT_THRESHOLD,
    DEFAULT_QUARANTINE_RETENTION_MS,
    DEFAULT_MAX_QUARANTINE_SIZE,
    DANGEROUS_PATTERNS,
    SUSPICIOUS_PATTERNS,
)


__all__ = [
    # Types - Enums
    "ApprovalStatus",
    "ApprovalScope",
    "GuardAction",
    "GuardDetector",
    "StallSignal",
    "FileEffects",
    "ApprovalDefault",
    "CommandAction",
    "CommandPolicyMode",
    "AuditAction",
    "RateDecisionStatus",
    # Types - Dataclasses
    "BudgetSpec",
    "BudgetState",
    "GuardConfig",
    "GuardVerdict",
    "Approval",
    "ApprovalGrant",
    "ApprovalContext",
    "CommandRule",
    "CommandDecision",
    "RateDecision",
    "AuditEvent",
    "QuotaLimits",
    "QuotaState",
    "StallState",
    # Types - Constants
    "ALLOW_CHOICES",
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "DEFAULT_RATE_WINDOW_MS",
    "DEFAULT_STALL_ITERATIONS",
    # Errors
    "PolicyError",
    "BudgetExceeded",
    "BudgetCostExceeded",
    "BudgetTokensExceeded",
    "QuotaExceeded",
    "SessionQuotaExceeded",
    "SandboxQuotaExceeded",
    "CostQuotaExceeded",
    "ApprovalError",
    "ApprovalNotFound",
    "ApprovalAlreadyResponded",
    "ApprovalTimedOut",
    "InvalidApprovalChoice",
    "ApprovalRecoveryFailed",
    "CommandDenied",
    "CommandRequiresApproval",
    "RateLimitExceeded",
    "GuardTripped",
    "LoopDetected",
    "StallDetected",
    "AuditWriteFailed",
    # Protocols
    "BudgetTrackerProtocol",
    "CostEstimatorProtocol",
    "ApprovalBrokerProtocol",
    "ApprovalGrantStoreProtocol",
    "ApprovalRecoveryProtocol",
    "LoopGuardProtocol",
    "StallGuardProtocol",
    "RateLimiterProtocol",
    "QuotaEnforcerProtocol",
    "CommandPolicyProtocol",
    "AuditLoggerProtocol",
    # Approvals
    "ApprovalBroker",
    "ApprovalGrantStore",
    "ApprovalRecovery",
    "InMemoryApprovalRecovery",
    "PostgresApprovalRecovery",
    # Budget
    "BudgetTracker",
    "CostEstimator",
    "ModelPricing",
    "ModelPrice",
    # Guards
    "LoopGuard",
    "StallGuard",
    "EnhancedStallGuard",
    "DEFAULT_RUN_STALL_BUDGET_MS",
    "DEFAULT_STALL_ROUND_LIMIT",
    "DEFAULT_HEARTBEAT_INTERVAL_MS",
    "DEFAULT_QUIET_THRESHOLD_MS",
    "call_hash",
    "canonicalize_args",
    # Rate Limiting
    "MemoryRateLimiter",
    "PostgresRateLimiter",
    # Quotas
    "QuotaEnforcer",
    "DEFAULT_QUOTA_LIMITS",
    "parse_quota_limits",
    # Command Policy
    "CommandPolicy",
    "CommandScanner",
    "DEFAULT_FLOOR_RULES",
    "create_rule",
    # Audit
    "AuditLogger",
    "InMemoryAuditLogger",
    "PostgresAuditLogger",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_FLUSH_INTERVAL_MS",
    "create_event",
    "format_event",
    # Sandbox Policy
    "SandboxPolicy",
    "SandboxPolicyConfig",
    "ToolClass",
    "TOOL_CLASSES",
    # Leader Lease
    "LeaderLease",
    "try_acquire_sweeper_lease",
    "run_with_approval_sweeper_lease",
    "APPROVAL_SWEEPER_LOCK_KEY",
    "STALE_SESSION_LOCK_KEY",
    "SCHEDULED_TASK_LOCK_KEY",
    # Security Screening
    "SecurityScreener",
    "RegexClassifier",
    "HeuristicClassifier",
    "QuarantineStore",
    "InMemoryQuarantineStore",
    "ThreatLevel",
    "ClassifierType",
    "ContentType",
    "SecurityVerdict",
    "QuarantinedContent",
    "PatternMatch",
    "DEFAULT_THREAT_THRESHOLD",
    "DEFAULT_QUARANTINE_RETENTION_MS",
    "DEFAULT_MAX_QUARANTINE_SIZE",
    "DANGEROUS_PATTERNS",
    "SUSPICIOUS_PATTERNS",
]
