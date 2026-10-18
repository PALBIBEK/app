# Python Patterns Quick Reference

Quick lookup for common patterns from the masterclass.

---

## Custom Descriptors

### classproperty
```python
from functools import update_wrapper
import typing as t

_T = t.TypeVar("_T")
_R_co = t.TypeVar("_R_co", covariant=True)

class classproperty(property, t.Generic[_R_co]):
    def __init__(self, func: t.Callable[[type[_T]], _R_co], /) -> None:
        self.func = func
        update_wrapper(self, func)

    def __get__(self, obj: _T | None, owner: type[_T] | None = None, /) -> _R_co:
        if owner is None and obj is not None:
            owner = type(obj)
        return self.func(owner)
```

**Usage:**
```python
class MyClass:
    @classproperty
    def config(cls):
        return {"name": cls.__name__}

# MyClass.config  # No () needed
```

---

## Hook System

```python
def run_hooks(cls, hook_name: str, *args, exclude_hooks: set[str] | None = None, **kwargs):
    """Run all methods matching _{hook_name}__* pattern"""
    exclude_hooks = set(exclude_hooks or [])
    hook_funcnames = sorted(
        funcname for funcname in dir(cls)
        if funcname.startswith(f"_{hook_name}__") and funcname not in exclude_hooks
    )
    for funcname in hook_funcnames:
        getattr(cls, funcname)(*args, **kwargs)
```

**Usage:**
```python
class User:
    def _before_save__normalize(self):
        self.email = self.email.lower()

    def _before_save__validate(self):
        if not self.email:
            raise ValueError("Email required")

run_hooks(user, "before_save")
```

---

## TYPE_CHECKING for Circular Imports

```python
import typing as t

if t.TYPE_CHECKING:
    from .models import User
    from .services import UserService

class Agent:
    def get_owner(self) -> "User":  # String literal
        ...
```

**With PEP 563:**
```python
from __future__ import annotations
import typing as t

if t.TYPE_CHECKING:
    from .models import User

class Agent:
    def get_owner(self) -> User:  # No quotes needed!
        ...
```

---

## Code Builder Pattern

```python
class CodeBuilder:
    def __init__(self, lang_spec: str):
        self._code_lines = []
        self._imports = set()

    def add_import(self, stmt: str):
        self._imports.add(stmt)
        return self

    def add_lines(self, lines: str | list[str]):
        if isinstance(lines, str):
            lines = lines.splitlines()
        self._code_lines.extend(lines)
        return self

    def build(self) -> str:
        parts = ["\n".join(sorted(self._imports)), "", *self._code_lines]
        return "\n".join(parts)
```

---

## Improved exec() with Tracebacks

```python
import linecache
import uuid

def exec_with_traceback(source_code: str, globals_dict=None, locals_dict=None, filename=None):
    if filename is None:
        filename = f"<generated-{uuid.uuid4().hex[:8]}>"

    # Register with linecache FIRST
    linecache.cache[filename] = (
        len(source_code),
        None,
        source_code.splitlines(keepends=True),
        filename
    )

    code = compile(source_code, filename, "exec", dont_inherit=True)
    exec(code, globals_dict or {}, locals_dict or globals_dict)
```

---

## SQLAlchemy Mixins

```python
from datetime import datetime
from sqlalchemy import Column, DateTime

class TimestampMixin:
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SoftDeleteMixin:
    deleted_at = Column(DateTime, nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

# Usage
class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    # Automatically has created_at, updated_at, deleted_at
```

---

## Dynamic Inner Classes

```python
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

class Versionable:
    @declared_attr
    def Version(cls):
        parent_cls = cls

        class VersionInner(Base):
            __tablename__ = f"{parent_cls.__tablename__}_version"

            parent_id = Column(Integer, ForeignKey(f"{parent_cls.__tablename__}.id"))
            parent = relationship(parent_cls, back_populates="versions")

        return VersionInner

    @declared_attr
    def versions(cls):
        return relationship(cls.Version, back_populates="parent")
```

---

## Factory Pattern

```python
from abc import ABC, abstractmethod

class LanguageSpec(ABC):
    name: str = None

    @classmethod
    def factory(cls, name: str):
        for subclass in cls.__subclasses__():
            if subclass.name == name:
                return subclass()
        raise ValueError(f"Unknown: {name}")

    @abstractmethod
    def make_literal(self, value, dtype) -> str:
        pass

class PythonSpec(LanguageSpec):
    name = "python"
    def make_literal(self, value, dtype):
        return repr(value)

# Usage
spec = LanguageSpec.factory("python")
```

---

## Immutable DTOs

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class UserDTO:
    id: int
    email: str
    created_at: datetime

    @classmethod
    def from_orm(cls, model):
        return cls(
            id=model.id,
            email=model.email,
            created_at=model.created_at
        )

    def to_dict(self):
        return dataclasses.asdict(self)
```

---

## Smart get_item()

```python
class BaseModel:
    @classmethod
    def get_item(cls, entity):
        from myapp import db

        if isinstance(entity, cls):
            return entity
        elif isinstance(entity, int):
            return db.session.get(cls, entity)
        elif isinstance(entity, str):
            # Check identity map first
            result = next(
                (obj for key, obj in db.session.identity_map.items()
                 if key[0] == cls and obj.name == entity),
                None
            )
            if result is None:
                result = db.session.query(cls).filter_by(name=entity).one()
            return result
        raise TypeError(f"Cannot get {cls} from {type(entity)}")
```

---

## Base Service

```python
from typing import Generic, TypeVar

T = TypeVar("T")

class BaseService(Generic[T]):
    model: type[T]

    def __init__(self, db_session):
        self.db = db_session

    def get_by_id(self, id: int) -> T:
        obj = self.db.get(self.model, id)
        if not obj:
            raise NotFoundError(f"{self.model.__name__} not found")
        return obj

    def list(self, offset=0, limit=100):
        return self.db.query(self.model).offset(offset).limit(limit).all()

class UserService(BaseService[User]):
    model = User
```

---

## Runtime Type Inspection

```python
from typing import get_origin, get_args

def is_optional(annotation):
    origin = get_origin(annotation)
    if origin is t.Union:
        return type(None) in get_args(annotation)
    return False

def unwrap_optional(annotation):
    if is_optional(annotation):
        args = get_args(annotation)
        return next(arg for arg in args if arg is not type(None))
    return annotation

# Usage
assert is_optional(Optional[str]) == True
assert unwrap_optional(Optional[str]) == str
```

---

## Covariant/Contravariant TypeVars

```python
from typing import TypeVar, Generic

_T_co = TypeVar("_T_co", covariant=True)      # For return types
_T_contra = TypeVar("_T_contra", contravariant=True)  # For params

class Reader(Generic[_T_co]):
    def read(self) -> _T_co: ...

class Writer(Generic[_T_contra]):
    def write(self, data: _T_contra) -> None: ...
```

---

## Custom Exceptions

```python
class AppException(Exception):
    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self):
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "context": self.context
        }

class NotFoundError(AppException): pass
class ValidationError(AppException): pass
```

---

## Test Factories

```python
from factory import Factory, Sequence, SubFactory
from factory.alchemy import SQLAlchemyModelFactory

class UserFactory(SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session = db.session

    id = Sequence(lambda n: n)
    email = Sequence(lambda n: f"user{n}@example.com")

class AgentFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Agent
        sqlalchemy_session = db.session

    created_by = SubFactory(UserFactory)

# Usage
user = UserFactory()
agent = AgentFactory(created_by=user)
```

---

## Cheat Sheet

| Pattern | When to Use | Key Benefit |
|---------|-------------|-------------|
| `classproperty` | Class-level computed values | Clean syntax without `()` |
| Hook System | Extensibility points | Decoupled plugins |
| `TYPE_CHECKING` | Circular imports | Zero runtime cost |
| Code Builder | Dynamic code gen | Language-agnostic |
| `exec_with_traceback` | Dynamic exec | Full tracebacks |
| Mixins | Reusable behaviors | DRY, composition |
| `@declared_attr` | Dynamic SQLAlchemy | Auto-generated relationships |
| Factory Pattern | Polymorphic creation | Open/closed principle |
| DTOs | Data transfer | Immutability, decoupling |
| Smart `get_item()` | Flexible lookup | Performance, convenience |
| TypeVars | Generic types | Type safety with variance |

---

## Import Checklist

```python
# Type system
import typing as t
from typing import TypeVar, Generic, Protocol, get_origin, get_args

# Async support
if t.TYPE_CHECKING:
    from typing import TYPE_CHECKING  # Not needed at runtime

# Dataclasses
from dataclasses import dataclass, field, asdict

# SQLAlchemy
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship, Mapped, mapped_column

# Code execution
import linecache
import uuid

# Utilities
from functools import wraps, update_wrapper
from abc import ABC, abstractmethod
```

---

## Common Pitfalls

❌ **Don't:**
- Forget `frozen=True` on DTOs
- Use `hasattr()` to check `__dict__` in descriptors
- Skip linecache registration before `exec()`
- Mix runtime and type-only imports
- Use invariant TypeVars for return types

✅ **Do:**
- Use factory methods for complex DTO construction
- Check identity map before querying
- Provide `to_dict()` and `from_dict()` on DTOs
- Use `@declared_attr` for class-referencing SQLAlchemy attributes
- Test mixins independently
