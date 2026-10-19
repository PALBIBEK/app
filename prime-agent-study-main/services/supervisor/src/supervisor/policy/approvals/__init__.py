"""Approvals subsystem — human-in-the-loop approval workflow.

Components:
- ApprovalBroker: Core approval request/response management
- ApprovalGrantStore: QM-style once/session/always grants
- ApprovalRecovery: Dual-layer recovery with DB and memory
- PostgresApprovalRecovery: PostgreSQL-backed recovery
- InMemoryApprovalRecovery: In-memory recovery for testing
"""

from supervisor.policy.approvals.broker import ApprovalBroker

__all__ = [
    "ApprovalBroker",
]
