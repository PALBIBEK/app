# API Routing Patterns

## Overview

This document covers API routing patterns in FastAPI, including route organization, URL conventions, dependency injection, and advanced routing techniques.

## Router Organization

### Central Router Pattern

```python
# app/web/routes.py
from fastapi import APIRouter, Depends
from app.web.dependencies import handle_api_auth, verify_license

def get_api_router() -> APIRouter:
    """
    Central API router with global dependencies and all sub-routers.
    """
    api_router = APIRouter(
        prefix="/api",
        dependencies=[
            Depends(handle_api_auth),     # Global authentication
            Depends(verify_license),       # License verification
        ]
    )

    # Include all feature routers
    for router in get_routers():
        api_router.include_router(router)

    return api_router

def get_routers() -> list[APIRouter]:
    """Get all routers to include in the API"""
    from app.web import (
        users,
        auth,
        pipelines,
        simulations,
        executions,
        roles,
        permissions,
    )

    return [
        auth.router,           # /auth/*
        users.router,          # /users/*
        roles.router,          # /roles/*
        permissions.router,    # /permissions/*
        pipelines.router,      # /pipelines/*
        simulations.router,    # /simulations/*
        executions.router,     # /executions/*
    ]
```

### Feature Router Pattern

```python
# app/web/users.py
from fastapi import APIRouter, Depends, status
from app.web.dependencies import get_current_user, get_db_session
from app.services.user_service import UserService
from app.web.schema.user_schema import UserSchema, UserCreateSchema

router = APIRouter(
    prefix="/users",
    tags=["users"],  # OpenAPI tag
    dependencies=[Depends(get_duckdb_connection)],
)

@router.get("", response_model=list[UserSchema])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    current_user = Depends(get_current_user),
):
    """List all users with pagination"""
    return UserService.list(skip=skip, limit=limit)

@router.get("/{user_id}", response_model=UserSchema)
async def get_user(
    user_id: int,
    current_user = Depends(get_current_user),
):
    """Get user by ID"""
    return UserService.get_by_id_with_auth(user_id, action="read")

@router.post("", response_model=UserSchema, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UserCreateSchema,
    current_user = Depends(get_current_user),
):
    """Create a new user"""
    return UserService.create(user)

@router.patch("/{user_id}", response_model=UserSchema)
async def update_user(
    user_id: int,
    user: UserCreateSchema,
    current_user = Depends(get_current_user),
):
    """Update existing user"""
    return UserService.update(user_id, user)

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    current_user = Depends(get_current_user),
):
    """Delete user"""
    UserService.delete(user_id)
```

## URL Conventions

### RESTful Resource URLs

```
# Resource collections
GET     /api/users              # List all users
POST    /api/users              # Create new user
GET     /api/users/{id}         # Get specific user
PATCH   /api/users/{id}         # Update user
DELETE  /api/users/{id}         # Delete user

# Nested resources
GET     /api/users/{id}/roles   # Get user's roles
POST    /api/users/{id}/roles   # Add role to user
DELETE  /api/users/{id}/roles/{role_id}  # Remove role from user

# Custom actions (use POST)
POST    /api/users/{id}/generate_api_key
POST    /api/users/{id}/reset_password
POST    /api/users/{id}/send_welcome_email
```

### URL Path Parameters

```python
# Single parameter
@router.get("/users/{user_id}")
async def get_user(user_id: int):
    pass

# Multiple parameters
@router.get("/users/{user_id}/roles/{role_id}")
async def get_user_role(user_id: int, role_id: int):
    pass

# Path parameter with validation
from pydantic import conint

@router.get("/users/{user_id}")
async def get_user(user_id: conint(gt=0)):  # Must be > 0
    pass
```

### Query Parameters

```python
from typing import Optional
from pydantic import conint, constr

@router.get("/users")
async def list_users(
    # Pagination
    skip: int = 0,
    limit: conint(gt=0, le=100) = 10,

    # Filtering
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,

    # Sorting
    sort_by: str = "created_date",
    sort_order: Literal["asc", "desc"] = "desc",
):
    """
    List users with filtering, pagination, and sorting.

    Query examples:
    - /users?skip=0&limit=10
    - /users?role=admin&is_active=true
    - /users?search=john&sort_by=name&sort_order=asc
    """
    pass
```

## Route Dependencies

### Authentication Dependency

```python
# app/web/dependencies/api_auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.auth_service import AuthService
from app.models.user import User

security = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    Dependency to get current authenticated user from JWT token.
    """
    token = credentials.credentials

    try:
        user = await AuthService.get_user_from_token(token)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
            )
        return user
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Dependency to ensure user is active.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user",
        )
    return current_user

async def get_current_admin_user(
    current_user: User = Depends(get_current_active_user)
) -> User:
    """
    Dependency to ensure user is admin.
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
```

### Database Session Dependency

```python
# app/web/dependencies/db_session.py
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.async_session import SQLAlchemyAsync

async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency to provide database session.
    Session is automatically closed after request.
    """
    async with SQLAlchemyAsync.async_session() as session:
        yield session

# Type annotation for dependency
from typing import Annotated
DBSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
```

### Permission Checking Dependency

```python
# app/web/dependencies/permissions.py
from typing import Callable
from fastapi import Depends, HTTPException, status
from app.models.user import User
from app.web.dependencies.api_auth import get_current_user

def require_permission(permission: str) -> Callable:
    """
    Dependency factory to check if user has specific permission.

    Usage:
        @router.get("/users", dependencies=[Depends(require_permission("users.read"))])
    """
    async def permission_checker(
        current_user: User = Depends(get_current_user)
    ):
        if not current_user.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission}",
            )
        return current_user

    return permission_checker

def require_any_permission(*permissions: str) -> Callable:
    """
    Dependency factory to check if user has any of the specified permissions.
    """
    async def permission_checker(
        current_user: User = Depends(get_current_user)
    ):
        if not any(current_user.has_permission(p) for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"One of these permissions required: {', '.join(permissions)}",
            )
        return current_user

    return permission_checker
```

## Auto-Generated CRUD Routes

### Entity API Mixin Pattern

```python
# app/web/mixins/entity_apis.py
from typing import Type, Optional
from fastapi import APIRouter, Depends, status
from app.services.base_service import BaseSqlAlchemyService
from app.web.schema.base_schema import BaseSqlAlchemySchema

class EntityAPIs:
    """
    Mixin to auto-generate CRUD routes for an entity.
    """

    # Override these in subclass
    service: Type[BaseSqlAlchemyService]
    schema: Type[BaseSqlAlchemySchema]
    list_schema: Type[BaseSqlAlchemySchema]
    entity_name: str = "entity"

    @classmethod
    def register(cls, router: APIRouter):
        """Register all CRUD routes on the given router"""

        # List endpoint
        @router.get("", response_model=list[cls.list_schema])
        async def list_entities(
            skip: int = 0,
            limit: int = 100,
        ):
            return cls.service.list(skip=skip, limit=limit)

        # Get endpoint
        @router.get("/{entity_id}", response_model=cls.schema)
        async def get_entity(entity_id: int):
            return cls.service.get_by_id_with_auth(
                entity_id,
                action="read"
            )

        # Create endpoint
        @router.post(
            "",
            response_model=cls.schema,
            status_code=status.HTTP_201_CREATED
        )
        async def create_entity(data: cls.schema):
            return cls.service.create(data)

        # Update endpoint
        @router.patch("/{entity_id}", response_model=cls.schema)
        async def update_entity(
            entity_id: int,
            data: cls.schema,
        ):
            return cls.service.update(entity_id, data)

        # Delete endpoint
        @router.delete(
            "/{entity_id}",
            status_code=status.HTTP_204_NO_CONTENT
        )
        async def delete_entity(entity_id: int):
            cls.service.delete(entity_id)

# Usage
class UserEntityAPIs(EntityAPIs):
    service = UserService
    schema = UserSchema
    list_schema = UserListSchema
    entity_name = "user"

UserEntityAPIs.register(router)
```

### Versionable API Mixin

```python
# app/web/mixins/versionable_apis.py
class VersionableAPIs:
    """
    Mixin to add version management routes.
    """

    @classmethod
    def register(cls, router: APIRouter):
        """Register version management routes"""

        # List versions
        @router.get("/{entity_id}/versions")
        async def list_versions(entity_id: int):
            return cls.service.get_versions(entity_id)

        # Get specific version
        @router.get("/{entity_id}/versions/{version_id}")
        async def get_version(entity_id: int, version_id: int):
            return cls.service.get_version(entity_id, version_id)

        # Create new version
        @router.post("/{entity_id}/versions")
        async def create_version(entity_id: int):
            return cls.service.create_version(entity_id)

        # Revert to version
        @router.post("/{entity_id}/versions/{version_id}/revert")
        async def revert_to_version(
            entity_id: int,
            version_id: int
        ):
            return cls.service.revert_to_version(entity_id, version_id)
```

### Workflowable API Mixin

```python
# app/web/mixins/workflowable_apis.py
class WorkflowableAPIs:
    """
    Mixin to add workflow management routes.
    """

    @classmethod
    def register(cls, router: APIRouter):
        """Register workflow routes"""

        # Submit for approval
        @router.post("/{entity_id}/submit")
        async def submit_for_approval(entity_id: int):
            return cls.service.submit_for_approval(entity_id)

        # Approve
        @router.post("/{entity_id}/approve")
        async def approve(
            entity_id: int,
            comment: Optional[str] = None
        ):
            return cls.service.approve(entity_id, comment)

        # Reject
        @router.post("/{entity_id}/reject")
        async def reject(
            entity_id: int,
            comment: str
        ):
            return cls.service.reject(entity_id, comment)

        # Get workflow status
        @router.get("/{entity_id}/workflow")
        async def get_workflow_status(entity_id: int):
            return cls.service.get_workflow_status(entity_id)
```

## Request/Response Models

### Request Body Validation

```python
from pydantic import BaseModel, EmailStr, constr, validator

class UserCreateRequest(BaseModel):
    """Request model for creating a user"""

    name: constr(min_length=1, max_length=255)
    email: EmailStr
    password: constr(min_length=8)
    role_ids: list[int] = []

    @validator("password")
    def validate_password(cls, v):
        """Ensure password has required complexity"""
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain digit")
        return v

    class Config:
        schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "john@example.com",
                "password": "SecurePass123",
                "role_ids": [1, 2],
            }
        }
```

### Response Models

```python
from datetime import datetime
from typing import Optional

class UserResponse(BaseModel):
    """Response model for user data"""

    id: int
    name: str
    email: str
    is_active: bool
    created_date: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True  # Enable ORM mode
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
```

### Generic Response Wrapper

```python
from typing import Generic, TypeVar, Optional

T = TypeVar("T")

class APIResponse(BaseModel, Generic[T]):
    """Generic API response wrapper"""

    data: Optional[T] = None
    error: Optional[dict] = None
    meta: dict = {}

class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated response"""

    items: list[T]
    total: int
    skip: int
    limit: int
    has_more: bool

@router.get("/users", response_model=PaginatedResponse[UserResponse])
async def list_users(skip: int = 0, limit: int = 10):
    users, total = UserService.list_paginated(skip, limit)

    return PaginatedResponse(
        items=users,
        total=total,
        skip=skip,
        limit=limit,
        has_more=(skip + limit) < total,
    )
```

## Advanced Routing Patterns

### Route Tagging and Documentation

```python
from enum import Enum

class Tags(str, Enum):
    """API tags for documentation"""
    USERS = "users"
    AUTH = "authentication"
    ADMIN = "administration"
    PIPELINES = "pipelines"

router = APIRouter(
    prefix="/users",
    tags=[Tags.USERS],
)

@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID",
    description="Retrieve a single user by their unique identifier",
    response_description="User details",
    responses={
        404: {"description": "User not found"},
        403: {"description": "Permission denied"},
    },
)
async def get_user(user_id: int):
    pass
```

### Background Tasks

```python
from fastapi import BackgroundTasks

def send_email(email: str, message: str):
    """Send email in background"""
    # Email sending logic
    pass

@router.post("/users")
async def create_user(
    user: UserCreateRequest,
    background_tasks: BackgroundTasks,
):
    """Create user and send welcome email"""
    new_user = UserService.create(user)

    # Send email in background
    background_tasks.add_task(
        send_email,
        new_user.email,
        "Welcome to our platform!"
    )

    return new_user
```

### File Uploads

```python
from fastapi import UploadFile, File

@router.post("/users/{user_id}/avatar")
async def upload_avatar(
    user_id: int,
    file: UploadFile = File(...),
):
    """Upload user avatar"""

    # Validate file type
    if file.content_type not in ["image/jpeg", "image/png"]:
        raise HTTPException(
            status_code=400,
            detail="Only JPEG and PNG images allowed"
        )

    # Save file
    contents = await file.read()
    file_path = await UserService.save_avatar(user_id, contents)

    return {"avatar_url": file_path}

@router.post("/users/import")
async def import_users(
    file: UploadFile = File(..., description="CSV file with user data"),
):
    """Bulk import users from CSV"""
    contents = await file.read()
    users = await UserService.import_from_csv(contents)

    return {"imported": len(users)}
```

### WebSocket Routes

```python
from fastapi import WebSocket, WebSocketDisconnect

@router.websocket("/ws/{user_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: int,
):
    """WebSocket connection for real-time updates"""
    await websocket.accept()

    try:
        while True:
            # Receive message
            data = await websocket.receive_text()

            # Process and send response
            response = await process_message(user_id, data)
            await websocket.send_json(response)

    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected")
```

## Best Practices

### 1. Route Organization
- Group related routes in separate files
- Use APIRouter for modularity
- Apply common dependencies at router level

### 2. URL Design
- Use nouns for resources, not verbs
- Keep URLs lowercase and use hyphens for multi-word
- Be consistent with plural vs singular

### 3. HTTP Methods
- GET for retrieval (idempotent, cacheable)
- POST for creation and custom actions
- PATCH for partial updates
- DELETE for removal

### 4. Status Codes
- 200 OK for successful GET/PATCH
- 201 Created for successful POST
- 204 No Content for successful DELETE
- 400 Bad Request for validation errors
- 401 Unauthorized for auth failures
- 403 Forbidden for permission denied
- 404 Not Found for missing resources
- 500 Internal Server Error for server errors

### 5. Dependencies
- Reuse dependencies across routes
- Use dependency injection for testability
- Chain dependencies for complex checks

## Conclusion

Well-structured API routing is essential for:
- API discoverability
- Code maintainability
- Developer experience
- Automatic documentation

The patterns shown here provide a scalable foundation for building comprehensive REST APIs with FastAPI.
