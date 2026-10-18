# Layered Architecture - Service Layer and Business Logic

## Overview

This document covers the layered architecture pattern, focusing on the service layer which sits between the web (API) layer and the data (repository) layer. The service layer contains business logic, authorization, and orchestration.

## Three-Layer Architecture

```
┌─────────────────────────────────┐
│      Web/API Layer              │  - HTTP handling
│  (Routes, Dependencies, Schemas)│  - Input validation
│                                 │  - Response serialization
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│      Service Layer              │  - Business logic
│   (Services, Authorization)     │  - Validation
│                                 │  - Orchestration
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│    Repository/Data Layer        │  - Database access
│   (Models, Repositories)        │  - Data persistence
│                                 │  - Relationships
└─────────────────────────────────┘
```

## Service Layer Fundamentals

### Base Service Class

```python
# app/services/base_service.py
from typing import Type, Optional, Any
from sqlalchemy import select
from app.db.models.base_model import BaseModel
from app.exception.base import RequestApiException

class BaseSqlAlchemyService:
    """
    Base service class for SQLAlchemy models.
    Provides common CRUD operations and lifecycle hooks.
    """

    # Override in subclass
    _sqla_model: Type[BaseModel]

    @classmethod
    def get_one(cls, entity, *, error=True):
        """
        Get single entity by ID, name, or instance.

        Args:
            entity: ID (int), name (str), or model instance
            error: Raise exception if not found

        Returns:
            Model instance or None
        """
        if entity is None:
            if error:
                raise RequestApiException(
                    f"{cls._sqla_model.__name__} not found"
                )
            return None

        # Already an instance
        if isinstance(entity, cls._sqla_model):
            return entity

        # Get by ID or name
        item = cls._sqla_model.get_item(entity)

        if item is None and error:
            raise RequestApiException(
                f"{cls._sqla_model.__name__} not found: {entity}"
            )

        return item

    @classmethod
    async def aget_one(cls, entity, *, error=True):
        """Async version of get_one"""
        if entity is None:
            if error:
                raise RequestApiException(
                    f"{cls._sqla_model.__name__} not found"
                )
            return None

        if isinstance(entity, cls._sqla_model):
            return entity

        from app.db.async_session import SQLAlchemyAsync

        async with SQLAlchemyAsync.async_session() as session:
            item = await cls._sqla_model.aget_item(entity, session)

        if item is None and error:
            raise RequestApiException(
                f"{cls._sqla_model.__name__} not found: {entity}"
            )

        return item

    @classmethod
    def get_all(cls, skip: int = 0, limit: int = 100):
        """Get all entities with pagination"""
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()
        stmt = select(cls._sqla_model).offset(skip).limit(limit)
        result = session.execute(stmt)

        return result.scalars().all()

    @classmethod
    def add_or_update(cls, item, background_tasks=None):
        """
        Add new or update existing item.
        Runs lifecycle hooks: pre_save_check, pre_save
        """
        # Run pre-save validation
        cls.pre_save_check(item)

        # Run pre-save hook
        cls.pre_save(item, background_tasks=background_tasks)

        # Save to database
        from app.db.async_session import SQLAlchemyAsync
        session = SQLAlchemyAsync.async_session()

        session.add(item)
        session.flush()  # Ensure ID is assigned
        session.commit()

        return item

    @classmethod
    def delete(cls, item):
        """
        Delete item.
        Runs lifecycle hooks: pre_delete_check, pre_delete
        """
        item = cls.get_one(item)

        # Run pre-delete validation
        cls.pre_delete_check(item)

        # Run pre-delete hook
        cls.pre_delete(item)

        # Delete from database
        from app.db.async_session import SQLAlchemyAsync
        session = SQLAlchemyAsync.async_session()

        session.delete(item)
        session.commit()

    # Lifecycle hooks (override in subclass)

    @classmethod
    def pre_save_check(cls, item):
        """
        Validation before saving.
        Raise RequestApiException if validation fails.
        """
        pass

    @classmethod
    def pre_save(cls, item, background_tasks=None):
        """
        Actions to perform before saving.
        Can modify item or trigger background tasks.
        """
        pass

    @classmethod
    def pre_delete_check(cls, item):
        """
        Validation before deletion.
        Raise RequestApiException if cannot delete.
        """
        pass

    @classmethod
    def pre_delete(cls, item):
        """
        Actions to perform before deletion.
        Can perform cleanup or cascade operations.
        """
        pass
```

### Concrete Service Example

```python
# app/services/user_service.py
from typing import Optional
from app.services.base_service import BaseSqlAlchemyService
from app.db.models.user import User
from app.exception.base import RequestApiException
from app.util.password import hash_password, verify_password

class UserService(BaseSqlAlchemyService):
    """Service for user operations"""

    _sqla_model = User

    @classmethod
    def create_user(cls, name: str, email: str, password: str, role_ids: list[int]):
        """Create a new user"""
        # Check if user already exists
        existing_user = cls.find_by_email(email)
        if existing_user:
            raise RequestApiException("User with this email already exists")

        # Hash password
        password_hash = hash_password(password)

        # Create user instance
        user = User(
            name=name,
            email=email,
            password_hash=password_hash,
        )

        # Add roles
        from app.services.role_service import RoleService
        user.roles = [RoleService.get_one(rid) for rid in role_ids]

        # Save
        return cls.add_or_update(user)

    @classmethod
    def update_user(cls, user_id: int, **kwargs):
        """Update user fields"""
        user = cls.get_by_id_with_auth(user_id, action="write")

        # Update allowed fields
        for field in ["name", "email", "is_active"]:
            if field in kwargs:
                setattr(user, field, kwargs[field])

        # Update password if provided
        if "password" in kwargs:
            user.password_hash = hash_password(kwargs["password"])

        return cls.add_or_update(user)

    @classmethod
    def find_by_email(cls, email: str) -> Optional[User]:
        """Find user by email"""
        from app.db.async_session import SQLAlchemyAsync
        from sqlalchemy import select

        session = SQLAlchemyAsync.async_session()
        stmt = select(User).where(User.email == email)
        result = session.execute(stmt)

        return result.scalar_one_or_none()

    @classmethod
    def authenticate(cls, email: str, password: str) -> Optional[User]:
        """Authenticate user with email and password"""
        user = cls.find_by_email(email)

        if not user:
            return None

        if not verify_password(password, user.password_hash):
            return None

        if not user.is_active:
            raise RequestApiException("User account is inactive")

        # Update last login
        from datetime import datetime
        user.last_login = datetime.utcnow()
        cls.add_or_update(user)

        return user

    @classmethod
    def generate_api_key(cls, user_id: int) -> str:
        """Generate new API key for user"""
        user = cls.get_by_id_with_auth(user_id, action="write")

        # Generate secure random key
        import secrets
        api_key = secrets.token_urlsafe(32)

        # Hash and store
        from app.util.password import hash_password
        user.api_key_hash = hash_password(api_key)

        cls.add_or_update(user)

        # Return plain key (only time it's visible)
        return api_key

    # Lifecycle hooks

    @classmethod
    def pre_save_check(cls, item: User):
        """Validate user before saving"""
        # Check email format
        import re
        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, item.email):
            raise RequestApiException("Invalid email format")

        # Check name length
        if len(item.name) < 2:
            raise RequestApiException("Name must be at least 2 characters")

        # Check for duplicate email (if email changed)
        if item.id:
            existing = cls.find_by_email(item.email)
            if existing and existing.id != item.id:
                raise RequestApiException("Email already in use")

    @classmethod
    def pre_delete_check(cls, item: User):
        """Validate before deleting user"""
        # Don't allow deleting yourself
        from app.context.app_user import AppUser
        current_user = AppUser.current_user.get()

        if current_user and current_user.name == item.name:
            raise RequestApiException("Cannot delete your own account")

        # Check if user has active sessions
        if item.has_active_sessions():
            raise RequestApiException(
                "Cannot delete user with active sessions"
            )

    @classmethod
    def pre_delete(cls, item: User):
        """Cleanup before deleting user"""
        # Delete user's API keys
        item.api_key_hash = None

        # Anonymize user data (GDPR compliance)
        item.email = f"deleted_{item.id}@example.com"
        item.name = f"Deleted User {item.id}"

        cls.add_or_update(item)
```

## Service Mixins

### Authorization Mixin

```python
# app/services/mixins/auth_mixin.py
from app.exception.base import RequestApiException

class WithAuthorizationService:
    """
    Mixin to add authorization checks to services.
    """

    # Override in subclass
    object_type = None  # e.g., "USER", "PIPELINE"

    @classmethod
    def get_by_id_with_auth(cls, entity_id, action: str):
        """
        Get entity by ID with authorization check.

        Args:
            entity_id: Entity identifier
            action: Action to perform (read, write, delete, execute)
        """
        item = cls.get_one(entity_id)
        cls.check_permission(item, action)
        return item

    @classmethod
    def check_permission(cls, obj, action: str):
        """
        Check if current user has permission to perform action.

        Raises:
            RequestApiException(403) if permission denied
        """
        from app.context.app_user import AppUser
        from app.services.permission_service import PermissionService

        current_user = AppUser.current_user.get()

        if not current_user:
            raise RequestApiException(
                "Authentication required",
                status_code=401
            )

        # Admin users have all permissions
        if current_user.is_admin:
            return

        # Check permission
        has_permission = PermissionService.check_permission(
            user=current_user,
            object_type=cls.object_type,
            object_id=obj.id,
            action=action,
        )

        if not has_permission:
            raise RequestApiException(
                f"Permission denied: {action} on {cls.object_type}",
                status_code=403
            )

    @classmethod
    def grant_permission(cls, obj, user, action: str):
        """Grant permission to user"""
        from app.services.permission_service import PermissionService

        PermissionService.grant_permission(
            user=user,
            object_type=cls.object_type,
            object_id=obj.id,
            action=action,
        )

    @classmethod
    def revoke_permission(cls, obj, user, action: str):
        """Revoke permission from user"""
        from app.services.permission_service import PermissionService

        PermissionService.revoke_permission(
            user=user,
            object_type=cls.object_type,
            object_id=obj.id,
            action=action,
        )
```

### Versionable Service Mixin

```python
# app/services/mixins/version_mixin.py
from typing import Optional

class VersionableService:
    """
    Mixin to add version control to services.
    """

    @classmethod
    def create_version(cls, entity_id: int, comment: Optional[str] = None):
        """Create a new version of entity"""
        item = cls.get_by_id_with_auth(entity_id, action="write")

        # Create version
        from app.db.models.version import Version

        version = Version(
            entity_type=cls._sqla_model.__name__,
            entity_id=item.id,
            data=item.to_dict(),
            comment=comment,
        )

        from app.db.async_session import SQLAlchemyAsync
        session = SQLAlchemyAsync.async_session()

        session.add(version)
        session.commit()

        return version

    @classmethod
    def get_versions(cls, entity_id: int):
        """Get all versions of entity"""
        item = cls.get_by_id_with_auth(entity_id, action="read")

        from app.db.models.version import Version
        from app.db.async_session import SQLAlchemyAsync
        from sqlalchemy import select

        session = SQLAlchemyAsync.async_session()
        stmt = (
            select(Version)
            .where(Version.entity_type == cls._sqla_model.__name__)
            .where(Version.entity_id == item.id)
            .order_by(Version.created_date.desc())
        )

        result = session.execute(stmt)
        return result.scalars().all()

    @classmethod
    def revert_to_version(cls, entity_id: int, version_id: int):
        """Revert entity to specific version"""
        item = cls.get_by_id_with_auth(entity_id, action="write")

        # Get version
        from app.db.models.version import Version
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()
        version = session.get(Version, version_id)

        if not version:
            raise RequestApiException("Version not found")

        if version.entity_id != item.id:
            raise RequestApiException("Version does not belong to this entity")

        # Create backup of current state
        cls.create_version(entity_id, comment="Auto-backup before revert")

        # Restore version data
        for key, value in version.data.items():
            if hasattr(item, key):
                setattr(item, key, value)

        return cls.add_or_update(item)
```

### Workflowable Service Mixin

```python
# app/services/mixins/workflow_mixin.py
from enum import Enum

class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"

class WorkflowableService:
    """
    Mixin to add approval workflow to services.
    """

    @classmethod
    def submit_for_approval(cls, entity_id: int):
        """Submit entity for approval"""
        item = cls.get_by_id_with_auth(entity_id, action="write")

        if item.workflow_status != WorkflowStatus.DRAFT:
            raise RequestApiException(
                "Only draft items can be submitted for approval"
            )

        item.workflow_status = WorkflowStatus.PENDING_APPROVAL

        # Notify approvers
        cls._notify_approvers(item)

        return cls.add_or_update(item)

    @classmethod
    def approve(cls, entity_id: int, comment: Optional[str] = None):
        """Approve entity"""
        item = cls.get_by_id_with_auth(entity_id, action="approve")

        if item.workflow_status != WorkflowStatus.PENDING_APPROVAL:
            raise RequestApiException(
                "Only pending items can be approved"
            )

        item.workflow_status = WorkflowStatus.APPROVED

        # Create approval record
        cls._create_approval_record(item, approved=True, comment=comment)

        # Notify submitter
        cls._notify_submitter(item, approved=True)

        return cls.add_or_update(item)

    @classmethod
    def reject(cls, entity_id: int, comment: str):
        """Reject entity"""
        item = cls.get_by_id_with_auth(entity_id, action="approve")

        if item.workflow_status != WorkflowStatus.PENDING_APPROVAL:
            raise RequestApiException(
                "Only pending items can be rejected"
            )

        item.workflow_status = WorkflowStatus.REJECTED

        # Create rejection record
        cls._create_approval_record(item, approved=False, comment=comment)

        # Notify submitter
        cls._notify_submitter(item, approved=False)

        return cls.add_or_update(item)

    @classmethod
    def _notify_approvers(cls, item):
        """Notify users who can approve"""
        from app.tasks.notification_tasks import send_approval_notification

        approvers = cls._get_approvers(item)
        for approver in approvers:
            send_approval_notification.delay(
                user_id=approver.id,
                entity_type=cls._sqla_model.__name__,
                entity_id=item.id,
            )

    @classmethod
    def _notify_submitter(cls, item, approved: bool):
        """Notify submitter of approval decision"""
        from app.tasks.notification_tasks import send_decision_notification

        send_decision_notification.delay(
            user_id=item.created_by,
            entity_type=cls._sqla_model.__name__,
            entity_id=item.id,
            approved=approved,
        )

    @classmethod
    def _create_approval_record(cls, item, approved: bool, comment: str):
        """Create approval/rejection record"""
        from app.db.models.approval import Approval
        from app.context.app_user import AppUser

        current_user = AppUser.current_user.get()

        approval = Approval(
            entity_type=cls._sqla_model.__name__,
            entity_id=item.id,
            approver_id=current_user.id,
            approved=approved,
            comment=comment,
        )

        from app.db.async_session import SQLAlchemyAsync
        session = SQLAlchemyAsync.async_session()

        session.add(approval)
        session.commit()

    @classmethod
    def _get_approvers(cls, item):
        """Get users who can approve this entity"""
        from app.services.user_service import UserService

        # Get users with approve permission
        return UserService.get_users_with_permission(
            object_type=cls.object_type,
            object_id=item.id,
            action="approve",
        )
```

## Service Composition

### Combining Mixins

```python
# app/services/pipeline_service.py
from app.services.base_service import BaseSqlAlchemyService
from app.services.mixins.auth_mixin import WithAuthorizationService
from app.services.mixins.version_mixin import VersionableService
from app.services.mixins.workflow_mixin import WorkflowableService
from app.db.models.pipeline import Pipeline

class PipelineService(
    BaseSqlAlchemyService,
    WithAuthorizationService,
    VersionableService,
    WorkflowableService,
):
    """
    Service for pipeline operations.
    Combines base CRUD, authorization, versioning, and workflow.
    """

    _sqla_model = Pipeline
    object_type = "PIPELINE"

    @classmethod
    def create_pipeline(cls, name: str, description: str, steps: list[dict]):
        """Create a new pipeline"""
        pipeline = Pipeline(
            name=name,
            description=description,
            steps=steps,
            workflow_status=WorkflowStatus.DRAFT,
        )

        # Auto-grant creator full permissions
        saved_pipeline = cls.add_or_update(pipeline)
        cls.grant_permission(saved_pipeline, current_user, "read")
        cls.grant_permission(saved_pipeline, current_user, "write")
        cls.grant_permission(saved_pipeline, current_user, "delete")

        return saved_pipeline

    @classmethod
    def execute_pipeline(cls, pipeline_id: int, inputs: dict):
        """Execute pipeline with given inputs"""
        pipeline = cls.get_by_id_with_auth(pipeline_id, action="execute")

        # Only approved pipelines can be executed
        if pipeline.workflow_status != WorkflowStatus.APPROVED:
            raise RequestApiException(
                "Only approved pipelines can be executed"
            )

        # Create execution record
        from app.services.execution_service import ExecutionService

        execution = ExecutionService.create_execution(
            pipeline_id=pipeline.id,
            inputs=inputs,
        )

        # Queue execution task
        from app.tasks.execution_tasks import execute_pipeline_task

        execute_pipeline_task.delay(execution.id)

        return execution

    # Lifecycle hooks

    @classmethod
    def pre_save_check(cls, item: Pipeline):
        """Validate pipeline before saving"""
        # Check name uniqueness
        from sqlalchemy import select
        from app.db.async_session import SQLAlchemyAsync

        session = SQLAlchemyAsync.async_session()
        stmt = select(Pipeline).where(Pipeline.name == item.name)

        if item.id:
            stmt = stmt.where(Pipeline.id != item.id)

        result = session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            raise RequestApiException("Pipeline name already exists")

        # Validate steps structure
        if not item.steps or len(item.steps) == 0:
            raise RequestApiException("Pipeline must have at least one step")

    @classmethod
    def pre_delete_check(cls, item: Pipeline):
        """Validate before deleting pipeline"""
        # Don't allow deleting if has active executions
        if item.has_active_executions():
            raise RequestApiException(
                "Cannot delete pipeline with active executions"
            )

    @classmethod
    def pre_delete(cls, item: Pipeline):
        """Cleanup before deleting pipeline"""
        # Delete all executions
        from app.services.execution_service import ExecutionService

        for execution in item.executions:
            ExecutionService.delete(execution)

        # Revoke all permissions
        from app.services.permission_service import PermissionService

        PermissionService.revoke_all_permissions(
            object_type=cls.object_type,
            object_id=item.id,
        )
```

## Business Logic Patterns

### Complex Validation

```python
@classmethod
def validate_pipeline_steps(cls, steps: list[dict]):
    """Validate pipeline step configuration"""
    seen_names = set()

    for idx, step in enumerate(steps):
        # Check required fields
        if "name" not in step:
            raise RequestApiException(
                f"Step {idx}: 'name' is required"
            )

        if "type" not in step:
            raise RequestApiException(
                f"Step {idx}: 'type' is required"
            )

        # Check unique names
        if step["name"] in seen_names:
            raise RequestApiException(
                f"Step {idx}: Duplicate step name '{step['name']}'"
            )

        seen_names.add(step["name"])

        # Validate step type
        valid_types = ["extract", "transform", "load"]
        if step["type"] not in valid_types:
            raise RequestApiException(
                f"Step {idx}: Invalid type '{step['type']}'"
            )

        # Validate dependencies
        if "depends_on" in step:
            for dep in step["depends_on"]:
                if dep not in seen_names:
                    raise RequestApiException(
                        f"Step {idx}: Unknown dependency '{dep}'"
                    )
```

### Cross-Entity Operations

```python
@classmethod
def clone_pipeline(cls, pipeline_id: int, new_name: str):
    """Clone existing pipeline"""
    # Get source pipeline
    source = cls.get_by_id_with_auth(pipeline_id, action="read")

    # Create copy
    clone = Pipeline(
        name=new_name,
        description=f"Clone of {source.name}",
        steps=source.steps.copy(),  # Deep copy
        workflow_status=WorkflowStatus.DRAFT,
    )

    # Save clone
    saved_clone = cls.add_or_update(clone)

    # Copy permissions
    from app.services.permission_service import PermissionService
    PermissionService.copy_permissions(
        source_type=cls.object_type,
        source_id=source.id,
        target_type=cls.object_type,
        target_id=saved_clone.id,
    )

    return saved_clone
```

## Best Practices

### 1. Service Organization
- One service per domain model
- Use mixins for cross-cutting concerns
- Keep services focused on business logic

### 2. Lifecycle Hooks
- Use `pre_save_check` for validation
- Use `pre_save` for side effects
- Use `pre_delete_check` to prevent invalid deletes
- Use `pre_delete` for cleanup

### 3. Authorization
- Always check permissions in service layer
- Use `get_by_id_with_auth` instead of `get_one`
- Grant appropriate permissions on creation

### 4. Error Handling
- Raise `RequestApiException` for business logic errors
- Provide clear, actionable error messages
- Use appropriate HTTP status codes

### 5. Testing
- Mock database in unit tests
- Test lifecycle hooks separately
- Test authorization checks

## Conclusion

A well-designed service layer is essential for:
- Centralizing business logic
- Enforcing authorization
- Maintaining data integrity
- Reusability across the application

The patterns shown here provide a robust foundation for building maintainable, secure, and testable business logic.
