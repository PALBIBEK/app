"""TenantPrincipal mapper tests (D1 MVP)."""

from prime_agent.db.models.user import User
from prime_agent.schemas.ai_identity import TenantPrincipal
from prime_agent.services.auth_service import AuthService


def test_tenant_principal_mapper_mvp_uses_user_id():
    user = User(
        email="mapper@example.com",
        name="Mapper",
        hashed_password=AuthService.hash_password("SecurePass123!"),
        role="member",
    )
    user.id = "user-abc-123"
    identity = TenantPrincipal.from_user(user)
    assert identity.tenant_id == "user-abc-123"
    assert identity.principal_id == "user-abc-123"
