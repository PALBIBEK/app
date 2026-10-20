# Ready-to-Use Code Templates

Copy-paste templates for common patterns.

---

## Template 1: Complete Model with Mixins

```python
# models/user.py
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.orm import relationship, Mapped, mapped_column

Base = declarative_base()

# ============== Mixins ==============

class TimestampMixin:
    """Adds created_at and updated_at"""
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditMixin:
    """Adds audit trail"""
    @declared_attr
    def created_by_id(cls):
        return Column(Integer, ForeignKey("users.id"))

    @declared_attr
    def created_by(cls):
        return relationship("User", foreign_keys=[cls.created_by_id])


class SoftDeleteMixin:
    """Soft delete capability"""
    deleted_at = Column(DateTime, nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self):
        self.deleted_at = datetime.utcnow()


# ============== Base Model ==============

class BaseModel(Base):
    __abstract__ = True

    id = Column(Integer, primary_key=True)

    @classmethod
    def get_item(cls, entity):
        """Polymorphic lookup by ID, name, or instance"""
        from myapp import db

        if isinstance(entity, cls):
            return entity
        elif isinstance(entity, int):
            obj = db.session.get(cls, entity)
        elif isinstance(entity, str):
            obj = db.session.query(cls).filter_by(name=entity).first()
        else:
            raise TypeError(f"Cannot get {cls} from {type(entity)}")

        if not obj:
            raise ValueError(f"{cls.__name__} not found: {entity}")
        return obj


# ============== Concrete Models ==============

class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False)
    username = Column(String(100), nullable=False)
    is_active = Column(Boolean, default=True)


class Article(Base, TimestampMixin, AuditMixin, SoftDeleteMixin):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    content = Column(String, nullable=False)
```

---

## Template 2: DTO with Factory Methods

```python
# dtos/user_dto.py
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

@dataclass(frozen=True)
class BaseDTO:
    """Base DTO with common methods"""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in field_names}
        return cls(**filtered)


@dataclass(frozen=True, repr=False)
class UserDTO(BaseDTO):
    """User data transfer object"""

    id: int
    email: str
    username: str
    is_active: bool
    created_at: datetime

    # Optional fields
    full_name: str | None = None

    def __repr__(self):
        return f"UserDTO(id={self.id}, username={self.username!r})"

    @classmethod
    def from_orm(cls, user_model):
        """Convert ORM model to DTO"""
        return cls(
            id=user_model.id,
            email=user_model.email,
            username=user_model.username,
            is_active=user_model.is_active,
            created_at=user_model.created_at,
            full_name=user_model.full_name
        )

    @classmethod
    def from_api_request(cls, data: dict):
        """Convert API request to DTO with validation"""
        required = {"email", "username"}
        if not required.issubset(data.keys()):
            raise ValueError(f"Missing: {required - data.keys()}")

        return cls(
            id=data.get("id", 0),
            email=data["email"].lower(),
            username=data["username"],
            is_active=data.get("is_active", True),
            created_at=data.get("created_at", datetime.utcnow()),
            full_name=data.get("full_name")
        )
```

---

## Template 3: Service Layer

```python
# services/base_service.py
from typing import Generic, TypeVar, Type
from sqlalchemy.orm import Session
from models.base import BaseModel

ModelType = TypeVar("ModelType", bound=BaseModel)

class BaseService(Generic[ModelType]):
    """Base service with CRUD operations"""

    model: Type[ModelType]

    def __init__(self, db_session: Session):
        self.db = db_session

    def get_by_id(self, id: int) -> ModelType:
        obj = self.db.get(self.model, id)
        if not obj:
            raise ValueError(f"{self.model.__name__} not found: {id}")
        return obj

    def list(self, *, offset: int = 0, limit: int = 100, **filters) -> list[ModelType]:
        query = self.db.query(self.model)

        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.filter(getattr(self.model, key) == value)

        return query.offset(offset).limit(limit).all()

    def create(self, **data) -> ModelType:
        obj = self.model(**data)
        self.db.add(obj)
        self.db.flush()
        return obj

    def update(self, id: int, **data) -> ModelType:
        obj = self.get_by_id(id)
        for key, value in data.items():
            if hasattr(obj, key):
                setattr(obj, key, value)
        self.db.flush()
        return obj

    def delete(self, id: int) -> None:
        obj = self.get_by_id(id)
        self.db.delete(obj)
        self.db.flush()


# services/user_service.py
from models.user import User
from .base_service import BaseService

class UserService(BaseService[User]):
    model = User

    def get_by_email(self, email: str) -> User:
        """Custom method for UserService"""
        user = self.db.query(User).filter_by(email=email).first()
        if not user:
            raise ValueError(f"User not found: {email}")
        return user

    def activate_user(self, user_id: int) -> User:
        """Custom business logic"""
        user = self.get_by_id(user_id)
        user.is_active = True
        self.db.flush()
        return user


# Usage
from database import get_db_session

with get_db_session() as session:
    user_service = UserService(session)

    # CRUD operations
    users = user_service.list(is_active=True, limit=50)
    user = user_service.get_by_id(1)
    new_user = user_service.create(email="test@test.com", username="test")
    updated = user_service.update(1, username="new_name")
    user_service.delete(1)

    # Custom methods
    user_by_email = user_service.get_by_email("test@test.com")
    user_service.activate_user(user_id=1)

    session.commit()
```

---

## Template 4: FastAPI Endpoint with Service

```python
# api/users.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from services.user_service import UserService
from dtos.user_dto import UserDTO

router = APIRouter(prefix="/users", tags=["users"])

def get_user_service(db: Session = Depends(get_db)) -> UserService:
    return UserService(db)

@router.get("/{user_id}", response_model=UserDTO)
def get_user(user_id: int, service: UserService = Depends(get_user_service)):
    """Get user by ID"""
    try:
        user = service.get_by_id(user_id)
        return UserDTO.from_orm(user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/", response_model=list[UserDTO])
def list_users(
    offset: int = 0,
    limit: int = 100,
    service: UserService = Depends(get_user_service)
):
    """List users with pagination"""
    users = service.list(offset=offset, limit=limit)
    return [UserDTO.from_orm(u) for u in users]

@router.post("/", response_model=UserDTO, status_code=201)
def create_user(data: dict, service: UserService = Depends(get_user_service)):
    """Create new user"""
    try:
        dto = UserDTO.from_api_request(data)
        user = service.create(**dto.to_dict())
        return UserDTO.from_orm(user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{user_id}", response_model=UserDTO)
def update_user(
    user_id: int,
    data: dict,
    service: UserService = Depends(get_user_service)
):
    """Update user"""
    try:
        user = service.update(user_id, **data)
        return UserDTO.from_orm(user)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, service: UserService = Depends(get_user_service)):
    """Delete user"""
    try:
        service.delete(user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

---

## Template 5: Code Builder

```python
# utils/code_builder.py
from typing import Protocol
from abc import ABC, abstractmethod

class LanguageSpec(Protocol):
    """Protocol for language-specific generation"""
    def make_literal(self, value, dtype: str) -> str: ...
    def define_function(self, name: str, body: str, inputs: list[str]) -> str: ...

class CodeBuilder:
    """Builder for generating code"""

    def __init__(self, lang_spec: LanguageSpec):
        self.lang = lang_spec
        self._imports: set[str] = set()
        self._code_lines: list[str] = []
        self._functions: dict[str, str] = {}

    def add_import(self, statement: str) -> "CodeBuilder":
        self._imports.add(statement)
        return self

    def add_line(self, line: str) -> "CodeBuilder":
        self._code_lines.append(line)
        return self

    def add_lines(self, lines: str | list[str]) -> "CodeBuilder":
        if isinstance(lines, str):
            lines = lines.splitlines()
        self._code_lines.extend(lines)
        return self

    def define_function(self, name: str, body: str, inputs: list[str]) -> "CodeBuilder":
        if name in self._functions:
            raise ValueError(f"Function {name} already defined")

        func_code = self.lang.define_function(name, body, inputs)
        self._functions[name] = func_code
        return self

    def build(self) -> str:
        """Assemble final code"""
        parts = []

        if self._imports:
            parts.append("\n".join(sorted(self._imports)))
            parts.append("")

        for func in self._functions.values():
            parts.append(func)
            parts.append("")

        if self._code_lines:
            parts.append("\n".join(self._code_lines))

        return "\n".join(parts)


class PythonSpec:
    """Python code generation"""

    def make_literal(self, value, dtype: str) -> str:
        return repr(value)

    def define_function(self, name: str, body: str, inputs: list[str]) -> str:
        input_str = ", ".join(inputs)
        indented_body = "\n".join("    " + line for line in body.splitlines())
        return f"def {name}({input_str}):\n{indented_body}"


# Usage
builder = CodeBuilder(PythonSpec())
builder.add_import("import json")
builder.add_import("from typing import Any")

builder.define_function(
    name="process_data",
    body="return json.dumps(data)",
    inputs=["data: dict"]
)

builder.add_line("result = process_data({'key': 'value'})")

code = builder.build()
print(code)
```

---

## Template 6: Celery Task with DTO

```python
# tasks/user_tasks.py
from celery import shared_task
import json
from services.user_service import UserService
from dtos.user_dto import UserDTO
from database import get_db_session

@shared_task
def send_welcome_email(user_dto_json: str) -> dict:
    """Send welcome email (async task)"""
    # Deserialize DTO
    data = json.loads(user_dto_json)
    user_dto = UserDTO.from_dict(data)

    # Send email (mock)
    print(f"Sending welcome email to {user_dto.email}")

    return {"status": "sent", "user_id": user_dto.id}

@shared_task
def cleanup_inactive_users(days: int = 30) -> dict:
    """Cleanup inactive users (periodic task)"""
    from datetime import datetime, timedelta

    with get_db_session() as session:
        service = UserService(session)
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Custom query (extend service as needed)
        inactive = session.query(User).filter(
            User.last_login < cutoff,
            User.is_active == True
        ).all()

        count = 0
        for user in inactive:
            service.update(user.id, is_active=False)
            count += 1

        session.commit()
        return {"deactivated": count}


# Usage
from tasks.user_tasks import send_welcome_email

# In your endpoint
@router.post("/users")
def create_user(data: dict):
    user_dto = UserDTO.from_api_request(data)
    user = user_service.create(**user_dto.to_dict())

    # Queue background task
    send_welcome_email.delay(user_dto.to_json())

    return {"id": user.id, "message": "User created, email queued"}
```

---

## Template 7: Test Suite with Factories

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models.base import Base
from database import get_db

@pytest.fixture(scope="session")
def engine():
    """Create test database engine"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine

@pytest.fixture
def db_session(engine):
    """Create database session for tests"""
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.rollback()
    session.close()

@pytest.fixture
def override_db(db_session):
    """Override database dependency"""
    def _get_db():
        yield db_session

    from main import app
    app.dependency_overrides[get_db] = _get_db
    yield
    app.dependency_overrides.clear()


# tests/factories.py
from factory import Factory, Sequence, SubFactory
from factory.alchemy import SQLAlchemyModelFactory
from models.user import User
from models.article import Article

class UserFactory(SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session_persistence = "commit"

    id = Sequence(lambda n: n)
    email = Sequence(lambda n: f"user{n}@example.com")
    username = Sequence(lambda n: f"user{n}")
    is_active = True

class ArticleFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Article
        sqlalchemy_session_persistence = "commit"

    id = Sequence(lambda n: n)
    title = Sequence(lambda n: f"Article {n}")
    content = "Test content"
    created_by = SubFactory(UserFactory)


# tests/test_user_service.py
from services.user_service import UserService
from tests.factories import UserFactory

def test_get_user_by_id(db_session):
    # Arrange
    user = UserFactory(username="testuser")
    service = UserService(db_session)

    # Act
    result = service.get_by_id(user.id)

    # Assert
    assert result.id == user.id
    assert result.username == "testuser"

def test_list_users(db_session):
    # Arrange
    UserFactory.create_batch(5)
    service = UserService(db_session)

    # Act
    users = service.list(limit=3)

    # Assert
    assert len(users) == 3

def test_create_user(db_session):
    # Arrange
    service = UserService(db_session)

    # Act
    user = service.create(email="new@example.com", username="newuser")

    # Assert
    assert user.id is not None
    assert user.email == "new@example.com"
```

---

## Template 8: Custom Exception Hierarchy

```python
# exceptions/base.py
import traceback
from typing import Any

class AppException(Exception):
    """Base exception for application"""

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "context": self.context
        }

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict())


class ValidationError(AppException):
    """Validation failed"""
    pass

class NotFoundError(AppException):
    """Resource not found"""
    pass

class UnauthorizedError(AppException):
    """User not authorized"""
    pass

class ExecutionError(AppException):
    """Code execution failed"""

    def __init__(self, message: str, code: str, line_number: int | None = None, **context):
        super().__init__(message, code=code, line_number=line_number, **context)
        self.code = code
        self.line_number = line_number


# Usage in FastAPI
from fastapi import HTTPException

@router.get("/users/{user_id}")
def get_user(user_id: int):
    try:
        user = service.get_by_id(user_id)
        return UserDTO.from_orm(user)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())
```

---

## Template 9: Engine Pattern

```python
# engines/base_engine.py
from abc import ABC, abstractmethod
from utils.code_builder import CodeBuilder

class BaseEngine(ABC):
    """Base engine for code generation"""

    @abstractmethod
    def generate(self, builder: CodeBuilder, dto: Any) -> CodeBuilder:
        """Generate code for this entity type"""
        pass


# engines/function_engine.py
from dataclasses import dataclass
from .base_engine import BaseEngine

@dataclass(frozen=True)
class FunctionDTO:
    id: int
    name: str
    code: str
    inputs: list[str]

class FunctionEngine(BaseEngine):
    """Generate code for user functions"""

    def generate(self, builder: CodeBuilder, dto: FunctionDTO) -> CodeBuilder:
        builder.define_function(
            name=f"function_{dto.id}",
            body=dto.code,
            inputs=dto.inputs
        )
        return builder


# Usage
from engines.function_engine import FunctionEngine, FunctionDTO
from utils.code_builder import CodeBuilder, PythonSpec

dto = FunctionDTO(
    id=1,
    name="calculate",
    code="return a + b",
    inputs=["a", "b"]
)

builder = CodeBuilder(PythonSpec())
engine = FunctionEngine()
engine.generate(builder, dto)

code = builder.build()
print(code)
```

---

## Quick Start Checklist

1. **Create project structure:**
   ```
   myapp/
   ├── models/
   │   ├── __init__.py
   │   ├── base.py          # BaseModel + mixins
   │   └── user.py          # User model
   ├── dtos/
   │   ├── __init__.py
   │   └── user_dto.py      # UserDTO
   ├── services/
   │   ├── __init__.py
   │   ├── base_service.py  # BaseService
   │   └── user_service.py  # UserService
   ├── api/
   │   ├── __init__.py
   │   └── users.py         # FastAPI routes
   ├── tasks/
   │   ├── __init__.py
   │   └── user_tasks.py    # Celery tasks
   └── tests/
       ├── __init__.py
       ├── conftest.py      # Fixtures
       ├── factories.py     # Factory classes
       └── test_users.py    # Tests
   ```

2. **Install dependencies:**
   ```bash
   pip install sqlalchemy fastapi pydantic celery factory-boy pytest
   ```

3. **Copy templates in order:**
   - Template 1: Models with mixins
   - Template 2: DTOs
   - Template 3: Services
   - Template 4: API endpoints
   - Template 5: Code builder (if needed)
   - Template 6: Background tasks
   - Template 7: Tests

4. **Customize as needed!**
