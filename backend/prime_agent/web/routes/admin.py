"""Control-plane admin routes gated by role."""

from fastapi import APIRouter

from prime_agent.web.dependencies import AdminUser

router = APIRouter()


@router.get("/ping")
async def admin_ping(current_user: AdminUser) -> dict[str, str]:
    """Lightweight admin gate used to prove require_role is enforced."""
    return {"status": "ok", "role": current_user.role}
