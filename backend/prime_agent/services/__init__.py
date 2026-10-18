"""
Service layer for Prime Agent.

Following corridor-platforms pattern with:
- Base service class
- Domain-specific services
- Lifecycle hooks
- Business logic encapsulation
"""
from prime_agent.services.ai_client import AiClient, AiPlatformNotReadyError, AiUpstreamError
from prime_agent.services.auth_service import AuthService
from prime_agent.services.base import BaseService
from prime_agent.services.harness_bind_service import HarnessBindService
from prime_agent.services.harness_service import HarnessService
from prime_agent.services.platform_status_service import PlatformStatusService
from prime_agent.services.session_service import SessionService
from prime_agent.services.tool_service import ToolService
from prime_agent.services.user_service import UserService

__all__ = [
    "AiClient",
    "AiPlatformNotReadyError",
    "AiUpstreamError",
    "BaseService",
    "AuthService",
    "HarnessBindService",
    "HarnessService",
    "PlatformStatusService",
    "SessionService",
    "ToolService",
    "UserService",
]
