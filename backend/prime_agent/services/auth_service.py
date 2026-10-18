"""
Authentication service for user registration and login.

Following production patterns:
- Argon2id password hashing (2026 standard)
- JWT token creation
- User authentication
"""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from prime_agent.config import get_settings
from prime_agent.db.models.refresh_token import RefreshToken
from prime_agent.db.models.user import User
from prime_agent.security.jwt_manager import JWTManager

# Password hashing context with Argon2id (2026 production standard)
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
# Always run a password verification for unknown accounts to reduce login
# timing differences that could otherwise disclose whether an email exists.
DUMMY_PASSWORD_HASH = pwd_context.hash("not-a-real-user-password")


class AuthService:
    """
    Service for authentication operations.

    Handles:
    - User registration
    - User authentication
    - Password hashing and verification
    - JWT token creation
    """

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using Argon2id.

        Args:
            password: Plain text password

        Returns:
            Hashed password string

        Security:
            - Argon2id (hybrid of Argon2i and Argon2d)
            - Resistant to side-channel and GPU attacks
            - Automatic salt generation
        """
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash.

        Args:
            plain_password: Plain text password to verify
            hashed_password: Previously hashed password

        Returns:
            True if password matches, False otherwise

        Security:
            - Constant-time comparison (timing attack prevention)
        """
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False

    @staticmethod
    def create_access_token(user_id: str, expires_delta: timedelta | None = None) -> str:
        """Create a short-lived access JWT via the claim-policy manager."""
        return JWTManager.create_access_token(user_id, expires_delta)

    @staticmethod
    def generate_refresh_token() -> str:
        """Generate an opaque refresh token using the OS CSPRNG."""
        return secrets.token_urlsafe(48)

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        """Hash a refresh token before persistence."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def decode_token(token: str) -> dict:
        """Decode and validate an access JWT."""
        return JWTManager.decode_access_token(token)

    @classmethod
    async def register_user(
        cls,
        email: str,
        password: str,
        name: str,
        db: AsyncSession
    ) -> User:
        """
        Register a new user.

        Args:
            email: User email (must be unique)
            password: Plain text password (will be hashed)
            name: User display name
            db: Database session

        Returns:
            Created user instance

        Raises:
            HTTPException: 400 if user already exists
        """
        normalized_email = email.strip().lower()

        # Check if user exists
        stmt = select(User).where(User.email == normalized_email)
        result = await db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unable to register with the provided credentials",
            )

        # Create user
        user = User(
            email=normalized_email,
            name=name,
            hashed_password=cls.hash_password(password),
            role="member",  # Default role
            is_active=True,
        )

        db.add(user)
        await db.commit()
        await db.refresh(user)

        return user

    @classmethod
    async def authenticate_user(
        cls,
        email: str,
        password: str,
        db: AsyncSession
    ) -> User:
        """
        Authenticate user with email and password.

        Args:
            email: User email
            password: Plain text password
            db: Database session

        Returns:
            User instance if authentication successful

        Raises:
            HTTPException: 401 if authentication fails
        """
        normalized_email = email.strip().lower()

        # Get user by email
        stmt = select(User).where(User.email == normalized_email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        # Check if user exists
        if not user:
            cls.verify_password(password, DUMMY_PASSWORD_HASH)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Verify password
        if not cls.verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Check if user is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive",
            )

        return user

    @classmethod
    async def issue_tokens(
        cls,
        user: User,
        db: AsyncSession,
        *,
        family_id: str | None = None,
    ) -> dict[str, str]:
        """Issue an access JWT and persist a hashed opaque refresh token."""
        settings = get_settings()
        raw_refresh_token = cls.generate_refresh_token()
        refresh_record = RefreshToken(
            user_id=user.id,
            family_id=family_id or str(uuid4()),
            token_hash=cls.hash_refresh_token(raw_refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )
        db.add(refresh_record)
        await db.commit()

        return {
            "access_token": cls.create_access_token(user.id),
            "refresh_token": raw_refresh_token,
            "token_type": "bearer",
        }

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Normalize timestamps returned by databases that drop timezone info."""
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @classmethod
    async def rotate_refresh_token(
        cls,
        raw_token: str,
        db: AsyncSession,
    ) -> dict[str, str]:
        """Rotate a refresh token and revoke its family when reuse is detected."""
        token_hash = cls.hash_refresh_token(raw_token)
        result = await db.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise cls._invalid_refresh_token()

        now = datetime.now(UTC)
        if record.used_at is not None or record.revoked_at is not None:
            await db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == record.family_id)
                .values(revoked_at=now)
            )
            await db.commit()
            raise cls._invalid_refresh_token()

        if cls._as_utc(record.expires_at) <= now:
            record.revoked_at = now
            await db.commit()
            raise cls._invalid_refresh_token()

        user = await cls.get_user_by_id(record.user_id, db)
        if user is None or not user.is_active:
            record.revoked_at = now
            await db.commit()
            raise cls._invalid_refresh_token()

        record.used_at = now
        return await cls.issue_tokens(user, db, family_id=record.family_id)

    @classmethod
    async def revoke_refresh_token(
        cls,
        raw_token: str,
        user_id: str,
        db: AsyncSession,
    ) -> None:
        """Revoke the matching refresh-token family owned by the user."""
        result = await db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == cls.hash_refresh_token(raw_token),
                RefreshToken.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if record is not None:
            await db.execute(
                update(RefreshToken)
                .where(
                    RefreshToken.family_id == record.family_id,
                    RefreshToken.user_id == user_id,
                    RefreshToken.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )
            await db.commit()

    @staticmethod
    def _invalid_refresh_token() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @classmethod
    async def get_user_by_id(
        cls,
        user_id: str,
        db: AsyncSession
    ) -> User | None:
        """
        Get user by ID.

        Args:
            user_id: User ID
            db: Database session

        Returns:
            User instance or None
        """
        result = await db.get(User, user_id)
        return result

    @classmethod
    async def get_current_user_from_token(
        cls,
        token: str,
        db: AsyncSession
    ) -> User:
        """
        Get current user from JWT token.

        Args:
            token: JWT access token
            db: Database session

        Returns:
            User instance

        Raises:
            HTTPException: 401 if token invalid or user not found
        """
        payload = cls.decode_token(token)
        user_id = payload.get("sub")
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        if not user_id:
            raise credentials_exception

        user = await cls.get_user_by_id(str(user_id), db)
        if not user:
            raise credentials_exception

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is inactive",
            )

        return user
