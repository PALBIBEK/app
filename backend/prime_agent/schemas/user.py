"""
User Pydantic schemas.

Matching frontend TypeScript types from:
- frontend/src/lib/api/types/auth.types.ts
"""
from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, EmailStr, Field

PASSWORD_MAX_LENGTH: Final = 256
"""Long enough for any passphrase or manager (NIST asks for at least 64), short enough
that hashing one stays cheap: a sign-in hashes whatever it is sent."""


class UserBase(BaseModel):
    """Base user schema with common fields."""

    email: EmailStr = Field(..., description="User email (unique)")
    name: str = Field(..., min_length=2, max_length=255, description="Full name")
    role: str = Field(default="member", description="User role: owner, admin, member")
    avatar: str | None = Field(None, max_length=512, description="Avatar URL")


class UserCreate(BaseModel):
    """Schema for user registration."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., description="User email (unique)")
    name: str = Field(..., min_length=2, max_length=255, description="Full name")
    avatar: str | None = Field(None, max_length=512, description="Avatar URL")
    password: str = Field(
        ..., min_length=12, max_length=PASSWORD_MAX_LENGTH, description="Password (12 to 256 characters)"
    )


class UserUpdate(BaseModel):
    """Schema for updating a user (all fields optional except password)."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr | None = None
    name: str | None = Field(None, min_length=2, max_length=255)
    avatar: str | None = Field(None, max_length=512)


class ChangePasswordRequest(BaseModel):
    """Authenticated password-change request."""

    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=12, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(..., min_length=12, max_length=PASSWORD_MAX_LENGTH)


class UserResponse(UserBase):
    """Schema for user response (excludes password)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class UserLogin(BaseModel):
    """Schema for login request."""

    email: EmailStr
    password: str = Field(..., min_length=12, max_length=PASSWORD_MAX_LENGTH)


class RefreshTokenRequest(BaseModel):
    """Refresh-token request body."""

    refresh_token: str = Field(..., min_length=1)


class AccessTokenResponse(BaseModel):
    """Response containing a newly issued access token."""

    access_token: str
    token_type: str = "bearer"
    csrf_token: str


class TokenResponse(BaseModel):
    """Schema for authentication token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthResponse(BaseModel):
    """Schema for authentication response (login/register)."""

    user: UserResponse
    access_token: str
    token_type: str = "bearer"
    csrf_token: str
