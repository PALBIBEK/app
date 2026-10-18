# Database Models and Schemas

## Overview

This document covers database modeling patterns using SQLAlchemy 2.0+ with async support, including ORM models, schema serialization with Marshmallow and Pydantic, and common model mixins.

## SQLAlchemy 2.0 ORM Models

### Base Model

```python
# app/db/models/base_model.py
from typing import Any, Optional
from sqlalchemy import BigInteger
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncAttrs

class SqlaBase(AsyncAttrs, DeclarativeBase):
    """Base for all SQLAlchemy declarative models"""
    pass

class BaseTable:
    """Custom table class with additional features"""
    pass

class BaseModel(SqlaBase):
    """
    Base model for all database models.
    Provides common functionality and ID column.
    """

    __abstract__ = True
    __table_cls__ = BaseTable

    # Primary key
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    @classmethod
    def get_item(cls, entity: Any) -> Optional["BaseModel"]:
        """
        Get item by ID, name, or instance.

        Args:
            entity: int (ID), str (name), or model instance

        Returns:
            Model instance or None
        """
        if entity is None:
            return None

        # Already an instance
        if isinstance(entity, cls):
            return entity

        from app.db.async_session import SQLAlchemyAsync
        from sqlalchemy import select

        session = SQLAlchemyAsync.async_session()

        # Get by ID
        if isinstance(entity, int):
            return session.get(cls, entity)

        # Get by name (if model has name column)
        if isinstance(entity, str) and hasattr(cls, "name"):
            stmt = select(cls).where(cls.name == entity)
            result = session.execute(stmt)
            return result.scalar_one_or_none()

        return None

    @classmethod
    async def aget_item(cls, entity: Any, db_session) -> Optional["BaseModel"]:
        """Async version of get_item"""
        if entity is None:
            return None

        if isinstance(entity, cls):
            return entity

        from sqlalchemy import select

        # Get by ID
        if isinstance(entity, int):
            return await db_session.get(cls, entity)

        # Get by name
        if isinstance(entity, str) and hasattr(cls, "name"):
            stmt = select(cls).where(cls.name == entity)
            result = await db_session.execute(stmt)
            return result.scalar_one_or_none()

        return None

    def to_dict(self) -> dict:
        """Convert model to dictionary"""
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
        }
```

### Model Example

```python
# app/db/models/user.py
from typing import Optional, List
from sqlalchemy import String, Boolean, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from app.db.models.base_model import BaseModel

class User(BaseModel):
    """User model"""

    __tablename__ = "user"

    # Columns
    name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    api_key_hash: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    last_login: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        nullable=True
    )

    # Relationships
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary="user_role_association",
        back_populates="users",
        lazy="selectin"  # Eager loading
    )

    created_pipelines: Mapped[List["Pipeline"]] = relationship(
        "Pipeline",
        back_populates="creator",
        foreign_keys="Pipeline.created_by"
    )

    # Hybrid properties
    @property
    def display_name(self) -> str:
        """User's display name"""
        return self.name

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated"""
        return self.is_active

    # Methods
    def has_role(self, role_name: str) -> bool:
        """Check if user has specific role"""
        return any(role.name == role_name for role in self.roles)

    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission"""
        return any(
            permission in role.permissions
            for role in self.roles
        )

    def __repr__(self):
        return f"<User(id={self.id}, name={self.name})>"
```

## Model Mixins

### Auditable Mixin

```python
# app/db/models/mixins/auditable.py
from typing import Optional
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

class Auditable:
    """
    Mixin to add audit fields to models.
    Tracks who created/modified the record and when.
    """

    # Created by
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id"),
        nullable=True
    )

    created_date: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    # Modified by
    last_modified_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id"),
        nullable=True
    )

    last_modified_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        onupdate=datetime.utcnow,
        nullable=True
    )

    # Relationships (use string references to avoid circular imports)
    created_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="selectin"
    )

    last_modified_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[last_modified_by],
        lazy="selectin"
    )
```

### Versionable Mixin

```python
# app/db/models/mixins/versionable.py
from typing import Optional
from sqlalchemy import Integer, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

class Versionable:
    """
    Mixin to add version tracking to models.
    """

    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False
    )

    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True
    )

    version_comment: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True
    )

    # Enable automatic version tracking via sqlalchemy-continuum
    __versioned__ = {
        "exclude": ["last_modified_date"]  # Don't track these fields
    }
```

### Workflowable Mixin

```python
# app/db/models/mixins/workflowable.py
from typing import Optional
from sqlalchemy import String, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from enum import Enum

class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"

class Workflowable:
    """
    Mixin to add workflow/approval status to models.
    """

    workflow_status: Mapped[WorkflowStatus] = mapped_column(
        SQLEnum(WorkflowStatus),
        default=WorkflowStatus.DRAFT,
        nullable=False
    )

    workflow_comment: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True
    )
```

### WithLogs Mixin

```python
# app/db/models/mixins/with_logs.py
from typing import List
from sqlalchemy.orm import Mapped, relationship

class WithLogs:
    """
    Mixin to add change log tracking to models.
    Uses sqlalchemy-continuum for automatic change tracking.
    """

    # Relationship to version history
    @property
    def versions(self) -> List:
        """Get all versions of this entity"""
        from sqlalchemy_continuum import version_class

        VersionModel = version_class(self.__class__)

        from app.db.async_session import SQLAlchemyAsync
        from sqlalchemy import select

        session = SQLAlchemyAsync.async_session()
        stmt = (
            select(VersionModel)
            .where(VersionModel.id == self.id)
            .order_by(VersionModel.transaction_id.desc())
        )

        result = session.execute(stmt)
        return result.scalars().all()

    def get_change_log(self) -> List[dict]:
        """Get formatted change log"""
        changes = []

        for version in self.versions:
            changes.append({
                "version": version.version,
                "changed_at": version.changed_at,
                "changed_by": version.changed_by,
                "changes": version.changeset,
            })

        return changes
```

## Relationships

### One-to-Many

```python
# app/db/models/pipeline.py
class Pipeline(BaseModel, Auditable):
    __tablename__ = "pipeline"

    name: Mapped[str] = mapped_column(String(255), unique=True)

    # Foreign key
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user.id"),
        nullable=False
    )

    # Relationship
    creator: Mapped["User"] = relationship(
        "User",
        back_populates="created_pipelines",
        foreign_keys=[created_by]
    )

    # One-to-many
    executions: Mapped[List["Execution"]] = relationship(
        "Execution",
        back_populates="pipeline",
        cascade="all, delete-orphan"  # Cascade delete
    )
```

### Many-to-Many

```python
# app/db/models/user_role.py
from sqlalchemy import Table, ForeignKey, Column, BigInteger

# Association table
user_role_association = Table(
    "user_role_association",
    BaseModel.metadata,
    Column("user_id", BigInteger, ForeignKey("user.id"), primary_key=True),
    Column("role_id", BigInteger, ForeignKey("role.id"), primary_key=True),
)

# User model
class User(BaseModel):
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary=user_role_association,
        back_populates="users"
    )

# Role model
class Role(BaseModel):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(100), unique=True)

    users: Mapped[List["User"]] = relationship(
        "User",
        secondary=user_role_association,
        back_populates="roles"
    )
```

### Self-Referential

```python
# app/db/models/pipeline.py
class Pipeline(BaseModel):
    __tablename__ = "pipeline"

    # Parent pipeline
    parent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pipeline.id"),
        nullable=True
    )

    # Self-referential relationship
    parent: Mapped[Optional["Pipeline"]] = relationship(
        "Pipeline",
        remote_side="Pipeline.id",
        back_populates="children"
    )

    children: Mapped[List["Pipeline"]] = relationship(
        "Pipeline",
        back_populates="parent"
    )
```

## Schema Serialization

### Marshmallow Schemas

```python
# app/web/schema/user_schema.py
from marshmallow import fields, validates, ValidationError, post_load
from marshmallow_sqlalchemy import SQLAlchemySchema, auto_field
from app.db.models.user import User
from app.web.schema.base_schema import BaseSqlAlchemySchema

class UserSchema(BaseSqlAlchemySchema):
    """Schema for User model"""

    class Meta:
        model = User
        load_instance = True  # Create model instances
        include_fk = True  # Include foreign keys
        ordered = True  # Deterministic output

    # Auto-mapped fields
    id = auto_field(dump_only=True)
    name = auto_field(required=True)
    email = auto_field(required=True)
    is_active = auto_field()
    is_admin = auto_field(dump_only=True)

    # Exclude sensitive fields
    password_hash = auto_field(load_only=True, required=False)
    api_key_hash = fields.String(load_only=True, required=False)

    # Nested relationships
    roles = fields.Nested("RoleSchema", many=True, dump_only=True)

    # Custom fields
    display_name = fields.Method("get_display_name")

    # Write-only field for password
    password = fields.String(load_only=True, required=False)

    def get_display_name(self, obj):
        """Get display name"""
        return obj.display_name

    @validates("email")
    def validate_email(self, value):
        """Validate email format"""
        import re
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(pattern, value):
            raise ValidationError("Invalid email format")

    @validates("password")
    def validate_password(self, value):
        """Validate password strength"""
        if value and len(value) < 8:
            raise ValidationError("Password must be at least 8 characters")

    @post_load
    def hash_password(self, data, **kwargs):
        """Hash password before saving"""
        if "password" in data:
            from app.util.password import hash_password
            data["password_hash"] = hash_password(data.pop("password"))
        return data
```

### Pydantic Schemas

```python
# app/web/schema/user_pydantic.py
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, EmailStr, constr, field_validator, ConfigDict

class UserBase(BaseModel):
    """Base user schema"""

    name: constr(min_length=1, max_length=255)
    email: EmailStr
    is_active: bool = True

class UserCreate(UserBase):
    """Schema for creating users"""

    password: constr(min_length=8)
    role_ids: List[int] = []

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        """Validate password strength"""
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain digit")
        return v

class UserUpdate(BaseModel):
    """Schema for updating users"""

    name: Optional[constr(min_length=1, max_length=255)] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    password: Optional[constr(min_length=8)] = None

class UserResponse(UserBase):
    """Schema for user responses"""

    id: int
    is_admin: bool
    last_login: Optional[datetime] = None
    created_date: datetime

    model_config = ConfigDict(
        from_attributes=True,  # Enable ORM mode
        json_encoders={
            datetime: lambda v: v.isoformat()
        }
    )

class UserListResponse(BaseModel):
    """Schema for listing users"""

    id: int
    name: str
    email: str
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
```

### Base Schema with Utilities

```python
# app/web/schema/base_schema.py
from marshmallow import Schema, post_dump
from marshmallow_sqlalchemy import SQLAlchemySchema

class BaseSchema(Schema):
    """Base schema with common utilities"""

    @post_dump
    def remove_none_values(self, data, **kwargs):
        """Remove None values from output"""
        return {
            key: value
            for key, value in data.items()
            if value is not None
        }

class BaseSqlAlchemySchema(SQLAlchemySchema, BaseSchema):
    """Base SQLAlchemy schema"""

    class Meta:
        load_instance = True
        include_fk = True
        ordered = True  # Deterministic output
```

### CamelCase Conversion

```python
# app/web/schema/base_web_model.py
from pydantic import BaseModel, ConfigDict

def to_camel(string: str) -> str:
    """Convert snake_case to camelCase"""
    components = string.split("_")
    return components[0] + "".join(x.title() for x in components[1:])

class BaseWebModel(BaseModel):
    """Base Pydantic model with camelCase conversion"""

    model_config = ConfigDict(
        alias_generator=to_camel,  # Auto camelCase
        populate_by_name=True,  # Accept both formats
        from_attributes=True,  # ORM mode
    )

# Usage
class UserResponse(BaseWebModel):
    id: int
    user_name: str  # Will be serialized as "userName"
    is_active: bool  # Will be serialized as "isActive"
```

## Custom Column Types

### JSON Column

```python
# app/db/custom_types.py
from sqlalchemy import TypeDecorator, Text
import json

class JSONString(TypeDecorator):
    """Store JSON as string in database"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Convert Python object to JSON string"""
        if value is not None:
            return json.dumps(value)
        return None

    def process_result_value(self, value, dialect):
        """Convert JSON string to Python object"""
        if value is not None:
            return json.loads(value)
        return None

# Usage
class Pipeline(BaseModel):
    __tablename__ = "pipeline"

    config: Mapped[dict] = mapped_column(JSONString, nullable=True)
    steps: Mapped[list] = mapped_column(JSONString, nullable=False)
```

### Encrypted Column

```python
from cryptography.fernet import Fernet
from sqlalchemy import TypeDecorator, String

class EncryptedString(TypeDecorator):
    """Store encrypted strings in database"""

    impl = String
    cache_ok = True

    def __init__(self, key: bytes, *args, **kwargs):
        self.fernet = Fernet(key)
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        """Encrypt value before storing"""
        if value is not None:
            return self.fernet.encrypt(value.encode()).decode()
        return None

    def process_result_value(self, value, dialect):
        """Decrypt value after retrieving"""
        if value is not None:
            return self.fernet.decrypt(value.encode()).decode()
        return None
```

## Best Practices

### 1. Model Design
- Use type hints for all columns
- Use appropriate column types and constraints
- Add indexes on frequently queried columns
- Use lazy loading strategically

### 2. Relationships
- Use `back_populates` for bidirectional relationships
- Configure cascade delete appropriately
- Use eager loading (`selectin`) for frequently accessed relationships

### 3. Mixins
- Create mixins for common patterns
- Use composition over inheritance
- Keep mixins focused on single responsibility

### 4. Schemas
- Separate request and response schemas
- Use `dump_only` for read-only fields
- Use `load_only` for write-only fields (passwords)
- Validate data at schema level

### 5. Serialization
- Choose Marshmallow for complex ORM scenarios
- Choose Pydantic for FastAPI integration
- Use consistent naming (camelCase or snake_case)

## Conclusion

Proper database modeling and serialization are critical for:
- Data integrity
- API consistency
- Developer experience
- Performance

The patterns shown here provide a solid foundation for building robust data layers with SQLAlchemy and modern Python type hints.
