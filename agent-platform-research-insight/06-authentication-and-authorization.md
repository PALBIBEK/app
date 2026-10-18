# Authentication and Authorization Patterns

## Overview

This document covers authentication and authorization patterns for FastAPI applications, including JWT tokens, API keys, OAuth2, SAML SSO, and role-based access control (RBAC).

## Authentication Methods

### 1. JWT Token Authentication

#### JWT Manager

```python
# app/web/dependencies/jwt_manager.py
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict
from app.config.settings import settings

class JWTManager:
    """Manage JWT token creation and validation"""

    SECRET_KEY = settings.SECRET_KEY
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE = timedelta(hours=8)
    REFRESH_TOKEN_EXPIRE = timedelta(days=7)

    @staticmethod
    def create_access_token(identity: dict, expires_delta: Optional[timedelta] = None) -> str:
        """
        Create JWT access token.

        Args:
            identity: User information to encode
            expires_delta: Optional custom expiration

        Returns:
            Encoded JWT token
        """
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + JWTManager.ACCESS_TOKEN_EXPIRE

        payload = {
            "identity": identity,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access",
        }

        return jwt.encode(
            payload,
            JWTManager.SECRET_KEY,
            algorithm=JWTManager.ALGORITHM
        )

    @staticmethod
    def create_refresh_token(identity: dict) -> str:
        """Create JWT refresh token"""
        expire = datetime.utcnow() + JWTManager.REFRESH_TOKEN_EXPIRE

        payload = {
            "identity": identity,
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh",
        }

        return jwt.encode(
            payload,
            JWTManager.SECRET_KEY,
            algorithm=JWTManager.ALGORITHM
        )

    @staticmethod
    def decode_token(token: str) -> Dict:
        """
        Decode and validate JWT token.

        Raises:
            jwt.ExpiredSignatureError: Token has expired
            jwt.InvalidTokenError: Token is invalid
        """
        return jwt.decode(
            token,
            JWTManager.SECRET_KEY,
            algorithms=[JWTManager.ALGORITHM]
        )

    @staticmethod
    def refresh_access_token(refresh_token: str) -> str:
        """
        Create new access token from refresh token.

        Args:
            refresh_token: Valid refresh token

        Returns:
            New access token
        """
        try:
            payload = JWTManager.decode_token(refresh_token)

            # Verify it's a refresh token
            if payload.get("type") != "refresh":
                raise jwt.InvalidTokenError("Not a refresh token")

            # Create new access token
            return JWTManager.create_access_token(payload["identity"])

        except jwt.ExpiredSignatureError:
            raise jwt.InvalidTokenError("Refresh token expired")
```

#### Auth Routes

```python
# app/web/auth.py
from fastapi import APIRouter, HTTPException, status, Response
from pydantic import BaseModel, EmailStr
from app.services.user_service import UserService
from app.web.dependencies.jwt_manager import JWTManager

router = APIRouter(prefix="/auth", tags=["authentication"])

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

@router.post("/login", response_model=TokenResponse)
async def login(credentials: LoginRequest, response: Response):
    """
    Authenticate user and return JWT tokens.
    Access token in response body, refresh token in HttpOnly cookie.
    """
    # Authenticate user
    user = UserService.authenticate(
        email=credentials.email,
        password=credentials.password
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    # Create tokens
    identity = {
        "user_id": user.id,
        "username": user.name,
        "email": user.email,
    }

    access_token = JWTManager.create_access_token(identity)
    refresh_token = JWTManager.create_refresh_token(identity)

    # Set refresh token in HttpOnly cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,  # HTTPS only
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=int(JWTManager.ACCESS_TOKEN_EXPIRE.total_seconds()),
    )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request):
    """Refresh access token using refresh token from cookie"""
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found",
        )

    try:
        access_token = JWTManager.refresh_access_token(refresh_token)

        return TokenResponse(
            access_token=access_token,
            expires_in=int(JWTManager.ACCESS_TOKEN_EXPIRE.total_seconds()),
        )

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

@router.post("/logout")
async def logout(response: Response):
    """Logout user by clearing refresh token cookie"""
    response.delete_cookie("refresh_token")
    return {"message": "Logged out successfully"}
```

#### Authentication Dependency

```python
# app/web/dependencies/api_auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.web.dependencies.jwt_manager import JWTManager
from app.services.user_service import UserService
from app.db.models.user import User
import jwt

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    Dependency to get current authenticated user from JWT token.
    """
    token = credentials.credentials

    try:
        # Decode token
        payload = JWTManager.decode_token(token)

        # Verify it's an access token
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type",
            )

        # Get user ID
        identity = payload.get("identity", {})
        user_id = identity.get("user_id")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
            )

        # Get user from database
        user = UserService.get_one(user_id, error=False)

        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        return user

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
```

### 2. API Key Authentication

```python
# app/web/dependencies/api_key_auth.py
from fastapi import Header, HTTPException, status
from typing import Optional
from app.services.user_service import UserService
from app.util.password import verify_password

async def verify_api_key(x_api_key: Optional[str] = Header(None)) -> User:
    """
    Dependency to authenticate via API key.

    Looks for API key in x-api-key header.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
        )

    # Find user by API key
    # Note: Store hashed API keys, never plain text
    user = UserService.find_by_api_key_hash(x_api_key)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    return user

# Combined auth: Try JWT, fall back to API key
async def get_current_user_hybrid(
    jwt_user: Optional[User] = Depends(get_current_user),
    api_key_user: Optional[User] = Depends(verify_api_key),
) -> User:
    """
    Authenticate via JWT or API key.
    """
    return jwt_user or api_key_user
```

### 3. OAuth2 Authentication

```python
# app/web/auth/oauth.py
from fastapi import APIRouter, HTTPException, Request
from authlib.integrations.starlette_client import OAuth
from app.config.settings import settings

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])

# Initialize OAuth
oauth = OAuth()

# Register Google OAuth
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Register Microsoft OAuth
oauth.register(
    name="microsoft",
    client_id=settings.MICROSOFT_CLIENT_ID,
    client_secret=settings.MICROSOFT_CLIENT_SECRET,
    authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    access_token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    client_kwargs={"scope": "openid email profile"},
)

@router.get("/google/login")
async def google_login(request: Request):
    """Redirect to Google OAuth login"""
    redirect_uri = request.url_for("google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@router.get("/google/callback")
async def google_callback(request: Request):
    """Handle Google OAuth callback"""
    try:
        # Get access token
        token = await oauth.google.authorize_access_token(request)

        # Get user info
        user_info = token.get("userinfo")

        if not user_info:
            raise HTTPException(
                status_code=400,
                detail="Failed to get user info"
            )

        # Find or create user
        user = UserService.find_or_create_oauth_user(
            email=user_info["email"],
            name=user_info.get("name", user_info["email"]),
            oauth_provider="google",
            oauth_id=user_info["sub"],
        )

        # Create JWT tokens
        identity = {
            "user_id": user.id,
            "username": user.name,
            "email": user.email,
        }

        access_token = JWTManager.create_access_token(identity)

        # Redirect to frontend with token
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?token={access_token}"
        )

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth authentication failed: {str(e)}"
        )
```

### 4. SAML SSO Authentication

```python
# app/web/auth/saml.py
from fastapi import APIRouter, Request, HTTPException
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from app.config.settings import settings

router = APIRouter(prefix="/auth/saml", tags=["saml"])

def init_saml_auth(request: Request):
    """Initialize SAML authentication"""
    saml_settings = {
        "sp": {
            "entityId": settings.SAML_SP_ENTITY_ID,
            "assertionConsumerService": {
                "url": str(request.url_for("saml_acs")),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
        },
        "idp": {
            "entityId": settings.SAML_IDP_ENTITY_ID,
            "singleSignOnService": {
                "url": settings.SAML_IDP_SSO_URL,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings.SAML_IDP_CERT,
        },
    }

    return OneLogin_Saml2_Auth(request, saml_settings)

@router.get("/login")
async def saml_login(request: Request):
    """Initiate SAML SSO login"""
    auth = init_saml_auth(request)
    return RedirectResponse(auth.login())

@router.post("/acs")
async def saml_acs(request: Request):
    """SAML Assertion Consumer Service (ACS)"""
    auth = init_saml_auth(request)

    # Process SAML response
    auth.process_response()

    errors = auth.get_errors()
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"SAML authentication failed: {', '.join(errors)}"
        )

    if not auth.is_authenticated():
        raise HTTPException(
            status_code=401,
            detail="SAML authentication failed"
        )

    # Get user attributes
    attributes = auth.get_attributes()
    email = attributes.get("email", [None])[0]
    name = attributes.get("name", [email])[0]

    # Find or create user
    user = UserService.find_or_create_saml_user(
        email=email,
        name=name,
        saml_attributes=attributes,
    )

    # Create JWT tokens
    identity = {
        "user_id": user.id,
        "username": user.name,
        "email": user.email,
    }

    access_token = JWTManager.create_access_token(identity)

    # Redirect to frontend
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={access_token}"
    )
```

## Authorization Patterns

### 1. Role-Based Access Control (RBAC)

#### Permission Model

```python
# app/db/models/permission.py
from sqlalchemy import String, BigInteger, ForeignKey, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models.base_model import BaseModel
from enum import Enum

class Action(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    EXECUTE = "execute"
    APPROVE = "approve"

class ObjectType(str, Enum):
    USER = "user"
    ROLE = "role"
    PIPELINE = "pipeline"
    SIMULATION = "simulation"

class Permission(BaseModel):
    """Permission model for RBAC"""

    __tablename__ = "permission"

    # Who has the permission
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        nullable=False
    )

    # What object type
    object_type: Mapped[ObjectType] = mapped_column(
        SQLEnum(ObjectType),
        nullable=False
    )

    # Which specific object (optional, None = all objects of type)
    object_id: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        nullable=True
    )

    # What action
    action: Mapped[Action] = mapped_column(
        SQLEnum(Action),
        nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="permissions")
```

#### Permission Service

```python
# app/services/permission_service.py
from app.services.base_service import BaseSqlAlchemyService
from app.db.models.permission import Permission, Action, ObjectType
from sqlalchemy import select, and_

class PermissionService(BaseSqlAlchemyService):
    """Service for permission operations"""

    _sqla_model = Permission

    @classmethod
    def check_permission(
        cls,
        user,
        object_type: ObjectType,
        object_id: int,
        action: Action
    ) -> bool:
        """
        Check if user has permission to perform action on object.

        Args:
            user: User instance
            object_type: Type of object
            object_id: ID of specific object
            action: Action to perform

        Returns:
            True if user has permission, False otherwise
        """
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()

        # Check for specific object permission or wildcard permission
        stmt = select(Permission).where(
            and_(
                Permission.user_id == user.id,
                Permission.object_type == object_type,
                Permission.action == action,
                (Permission.object_id == object_id) | (Permission.object_id == None)
            )
        )

        result = session.execute(stmt)
        permission = result.scalar_one_or_none()

        return permission is not None

    @classmethod
    def grant_permission(
        cls,
        user,
        object_type: ObjectType,
        object_id: Optional[int],
        action: Action
    ):
        """Grant permission to user"""
        # Check if permission already exists
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()

        stmt = select(Permission).where(
            and_(
                Permission.user_id == user.id,
                Permission.object_type == object_type,
                Permission.object_id == object_id,
                Permission.action == action,
            )
        )

        result = session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return existing

        # Create new permission
        permission = Permission(
            user_id=user.id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )

        return cls.add_or_update(permission)

    @classmethod
    def revoke_permission(
        cls,
        user,
        object_type: ObjectType,
        object_id: Optional[int],
        action: Action
    ):
        """Revoke permission from user"""
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()

        stmt = select(Permission).where(
            and_(
                Permission.user_id == user.id,
                Permission.object_type == object_type,
                Permission.object_id == object_id,
                Permission.action == action,
            )
        )

        result = session.execute(stmt)
        permission = result.scalar_one_or_none()

        if permission:
            cls.delete(permission)
```

### 2. Context-Based User Management

```python
# app/context/app_user.py
from contextvars import ContextVar
from typing import Optional
from app.db.models.user import User

class AppUser:
    """
    Thread-safe user context using context variables.
    Tracks current user across async calls.
    """

    current_user: ContextVar[Optional[User]] = ContextVar(
        "current_user",
        default=None
    )

    def __init__(self, user: User):
        self.user = user
        self.token = None

    def __enter__(self):
        """Set current user in context"""
        self.token = self.current_user.set(self.user)
        return self

    def __exit__(self, *args):
        """Reset context"""
        self.current_user.reset(self.token)

    @classmethod
    def get_current_user(cls) -> Optional[User]:
        """Get current user from context"""
        return cls.current_user.get()

# Usage
from app.context.app_user import AppUser

with AppUser(user=some_user):
    # All operations in this context see some_user as current
    result = UserService.create_pipeline(...)
```

### 3. Permission Dependency

```python
# app/web/dependencies/permissions.py
from typing import Callable
from fastapi import Depends, HTTPException, status
from app.db.models.user import User
from app.web.dependencies.api_auth import get_current_user
from app.services.permission_service import PermissionService
from app.db.models.permission import ObjectType, Action

def require_permission(
    object_type: ObjectType,
    action: Action,
    object_id_param: str = "id"
) -> Callable:
    """
    Dependency factory to check object-level permission.

    Args:
        object_type: Type of object
        action: Action to perform
        object_id_param: Path parameter name for object ID

    Returns:
        Dependency function

    Usage:
        @router.get("/{id}", dependencies=[
            Depends(require_permission(ObjectType.PIPELINE, Action.READ))
        ])
    """
    async def permission_checker(
        request: Request,
        current_user: User = Depends(get_current_user)
    ):
        # Extract object ID from path parameters
        object_id = request.path_params.get(object_id_param)

        if not object_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing {object_id_param} parameter"
            )

        # Check permission
        has_permission = PermissionService.check_permission(
            user=current_user,
            object_type=object_type,
            object_id=int(object_id),
            action=action,
        )

        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {action} on {object_type}"
            )

        return current_user

    return permission_checker

# Usage in routes
@router.get(
    "/pipelines/{id}",
    dependencies=[
        Depends(require_permission(ObjectType.PIPELINE, Action.READ))
    ]
)
async def get_pipeline(id: int):
    pass
```

## Best Practices

### 1. Authentication
- Always hash passwords (bcrypt, argon2)
- Use HTTPS for all authentication endpoints
- Implement rate limiting on auth endpoints
- Use short-lived access tokens
- Store refresh tokens in HttpOnly cookies
- Implement token rotation

### 2. Authorization
- Check permissions in service layer, not just routes
- Use principle of least privilege
- Implement object-level permissions
- Audit permission changes
- Regular permission review

### 3. Security
- Never log sensitive data (passwords, tokens)
- Validate JWT signatures
- Check token expiration
- Implement account lockout after failed attempts
- Use secure session management

### 4. Multi-Factor Authentication (MFA)

```python
# app/services/mfa_service.py
import pyotp
import qrcode
from io import BytesIO

class MFAService:
    """Multi-factor authentication service"""

    @staticmethod
    def generate_secret() -> str:
        """Generate TOTP secret"""
        return pyotp.random_base32()

    @staticmethod
    def get_totp_uri(secret: str, email: str) -> str:
        """Get TOTP provisioning URI for QR code"""
        return pyotp.totp.TOTP(secret).provisioning_uri(
            name=email,
            issuer_name="Your App"
        )

    @staticmethod
    def verify_totp(secret: str, token: str) -> bool:
        """Verify TOTP token"""
        totp = pyotp.TOTP(secret)
        return totp.verify(token, valid_window=1)

    @staticmethod
    def generate_qr_code(uri: str) -> bytes:
        """Generate QR code image"""
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(uri)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")

        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
```

## Conclusion

Robust authentication and authorization are critical for:
- Application security
- User privacy
- Compliance (GDPR, HIPAA, etc.)
- Audit trails

The patterns shown here provide a comprehensive foundation for implementing secure authentication and fine-grained authorization in FastAPI applications.
