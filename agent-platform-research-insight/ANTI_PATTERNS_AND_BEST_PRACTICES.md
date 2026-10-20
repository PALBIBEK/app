# Anti-Patterns and Best Practices

What to avoid and what to do instead when building production Python applications.

---

## Table of Contents

1. [Architecture Anti-Patterns](#architecture-anti-patterns)
2. [ORM Anti-Patterns](#orm-anti-patterns)
3. [Type System Anti-Patterns](#type-system-anti-patterns)
4. [Code Generation Anti-Patterns](#code-generation-anti-patterns)
5. [Testing Anti-Patterns](#testing-anti-patterns)
6. [Performance Anti-Patterns](#performance-anti-patterns)
7. [Security Anti-Patterns](#security-anti-patterns)
8. [Best Practices Summary](#best-practices-summary)

---

## Architecture Anti-Patterns

### ❌ Fat Models (God Objects)

**Bad:**
```python
class User(Base):
    # Model doing everything
    def send_email(self):
        ...
    def generate_report(self):
        ...
    def process_payment(self):
        ...
    def validate_complex_business_logic(self):
        ...
```

**Good:**
```python
# Model: Just data + simple properties
class User(Base):
    id = Column(Integer, primary_key=True)
    email = Column(String)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

# Service: Business logic
class UserService:
    def send_welcome_email(self, user: User):
        ...

# Separate services
class ReportService:
    def generate_user_report(self, user: User):
        ...

class PaymentService:
    def process_payment(self, user: User, amount: float):
        ...
```

**Why:**
- Models should represent **data structure**, not business logic
- Single Responsibility Principle
- Easier testing (mock services, not ORM)
- Better separation of concerns

---

### ❌ Circular Dependencies

**Bad:**
```python
# user.py
from .organization import Organization

class User:
    org: Organization

# organization.py
from .user import User

class Organization:
    owner: User  # Circular import!
```

**Good:**
```python
# user.py
import typing as t

if t.TYPE_CHECKING:
    from .organization import Organization

class User:
    org: "Organization"

# organization.py
import typing as t

if t.TYPE_CHECKING:
    from .user import User

class Organization:
    owner: "User"
```

**Why:**
- Runtime imports break
- Type checkers still work with `TYPE_CHECKING`
- Zero runtime cost

---

### ❌ Service Methods Returning ORM Models

**Bad:**
```python
class UserService:
    def get_user(self, id: int) -> User:
        # Returns ORM model directly
        return db.session.get(User, id)

# API layer
@router.get("/users/{id}")
def get_user(id: int):
    user = user_service.get_user(id)
    # Exposed to ORM internals, lazy loading issues
    return user
```

**Good:**
```python
class UserService:
    def get_user(self, id: int) -> UserDTO:
        # Returns DTO
        user = db.session.get(User, id)
        return UserDTO.from_orm(user)

# API layer
@router.get("/users/{id}")
def get_user(id: int):
    user_dto = user_service.get_user(id)
    # Clean DTO, no ORM issues
    return user_dto.to_dict()
```

**Why:**
- DTOs are serializable (JSON, pickle for Celery)
- No lazy loading issues
- Decoupling from ORM internals
- Explicit about what data crosses boundaries

---

### ❌ Direct Database Access in Routes

**Bad:**
```python
@router.get("/users/{id}")
def get_user(id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter_by(id=id).first()
    if not user:
        raise HTTPException(404)
    return user
```

**Good:**
```python
@router.get("/users/{id}")
def get_user(id: int, service: UserService = Depends(get_user_service)):
    try:
        return service.get_user(id)
    except NotFoundError:
        raise HTTPException(404)
```

**Why:**
- Business logic in one place (service)
- Easier to test (mock service)
- Easier to change database layer
- Consistent error handling

---

## ORM Anti-Patterns

### ❌ N+1 Query Problem

**Bad:**
```python
# Fetches users
users = db.query(User).all()

# For each user, separate query for organization (N+1)
for user in users:
    print(user.organization.name)  # Lazy load!
```

**Good:**
```python
# Eager loading with joinedload
from sqlalchemy.orm import joinedload

users = db.query(User).options(joinedload(User.organization)).all()

# No additional queries
for user in users:
    print(user.organization.name)
```

**Why:**
- 1 query instead of N+1
- Massive performance improvement
- Predictable query count

---

### ❌ Not Using Identity Map

**Bad:**
```python
# Query 1
user = db.query(User).filter_by(id=1).first()

# Query 2 (unnecessary!)
same_user = db.query(User).filter_by(id=1).first()
```

**Good:**
```python
# Query 1
user = db.session.get(User, 1)  # Uses identity map

# No query (returns cached object)
same_user = db.session.get(User, 1)

assert user is same_user  # True (same object)
```

**Why:**
- `session.get()` checks identity map first
- Fewer database queries
- Consistent object identity

---

### ❌ Catching Too Generic Exceptions

**Bad:**
```python
try:
    user = User.get_item(999)
except Exception:
    # Catches everything, even programming errors!
    return None
```

**Good:**
```python
from sqlalchemy.orm.exc import NoResultFound

try:
    user = User.get_item(999)
except NoResultFound:
    # Only catches expected exception
    return None
# Let other exceptions propagate
```

**Why:**
- Don't hide programming errors
- Explicit about what you're handling
- Easier debugging

---

### ❌ Mutable Default Arguments

**Bad:**
```python
def create_user(tags: list = []):  # BUG!
    user = User()
    user.tags = tags
    return user

# All users share same list!
user1 = create_user()
user2 = create_user()
user1.tags.append("admin")
assert "admin" in user2.tags  # True! Bug!
```

**Good:**
```python
def create_user(tags: list | None = None):
    if tags is None:
        tags = []
    user = User()
    user.tags = tags
    return user
```

**Why:**
- Default arguments evaluated once at function definition
- Mutable defaults shared across calls
- Use `None` as sentinel, create new object in function

---

## Type System Anti-Patterns

### ❌ Using `Any` Instead of Proper Types

**Bad:**
```python
from typing import Any

def process_data(data: Any) -> Any:
    # Type checker gives up here
    return data.transform()  # No autocomplete, no type checking
```

**Good:**
```python
from typing import Protocol

class Transformable(Protocol):
    def transform(self) -> "TransformResult":
        ...

def process_data(data: Transformable) -> TransformResult:
    # Type checker knows about transform()
    return data.transform()
```

**Why:**
- `Any` disables type checking
- Use `Protocol` for duck typing
- Get autocomplete and error detection

---

### ❌ String Annotations Everywhere

**Bad:**
```python
def get_user(id: int) -> "User":
    ...

def get_org(id: int) -> "Organization":
    ...

def get_project(id: int) -> "Project":
    ...
```

**Good:**
```python
from __future__ import annotations  # Top of file

def get_user(id: int) -> User:
    ...

def get_org(id: int) -> Organization:
    ...

def get_project(id: int) -> Project:
    ...
```

**Why:**
- PEP 563: All annotations become strings automatically
- Cleaner code
- Forward references work naturally

---

### ❌ Not Using Generic TypeVars

**Bad:**
```python
class Container:
    def get(self) -> Any:  # Lost type information
        ...

# No type safety
result: str = container.get()  # Might not be str!
```

**Good:**
```python
from typing import Generic, TypeVar

T = TypeVar("T")

class Container(Generic[T]):
    def get(self) -> T:
        ...

# Type safe
container: Container[str] = ...
result: str = container.get()  # Type checker knows it's str
```

**Why:**
- Preserve type information through generics
- Type checker can verify correctness
- Better autocomplete

---

## Code Generation Anti-Patterns

### ❌ Using exec() Without Linecache

**Bad:**
```python
code = "def foo(): return 42"
exec(code)

# Error traceback shows no source:
# File "<string>", line 1
```

**Good:**
```python
import linecache
import uuid

code = "def foo(): return 42"
filename = f"<generated-{uuid.uuid4().hex[:8]}>"

linecache.cache[filename] = (
    len(code), None, code.splitlines(True), filename
)

exec(compile(code, filename, "exec"))

# Error traceback shows actual source:
# File "<generated-a1b2c3d4>", line 1
# def foo(): return 42
```

**Why:**
- Tracebacks show actual source code
- Debugging is possible
- Better error messages for users

---

### ❌ String Concatenation for Code

**Bad:**
```python
code = "def " + func_name + "(" + params + "):\n"
code += "    return " + body
```

**Good:**
```python
from textwrap import dedent

code = dedent(f'''
def {func_name}({params}):
    return {body}
''').strip()
```

**Why:**
- Readable
- Handles indentation correctly
- Easier to maintain

---

### ❌ Not Validating Generated Code

**Bad:**
```python
def generate_function(name: str, body: str):
    # No validation!
    return f"def {name}(): {body}"

# Generates invalid code
code = generate_function("foo", "return")
```

**Good:**
```python
import ast

def generate_function(name: str, body: str) -> str:
    code = f"def {name}(): {body}"

    # Validate syntax
    try:
        ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"Generated invalid code: {e}")

    return code
```

**Why:**
- Catch errors early
- Better error messages
- Validate before execution

---

## Testing Anti-Patterns

### ❌ Testing Implementation Details

**Bad:**
```python
def test_user_service():
    service = UserService(db)

    # Testing internal method (implementation detail)
    assert service._validate_email("test@test.com") == True
```

**Good:**
```python
def test_user_service():
    service = UserService(db)

    # Test public API
    user = service.create_user(email="test@test.com")
    assert user.email == "test@test.com"

    # Test behavior, not implementation
    with pytest.raises(ValidationError):
        service.create_user(email="invalid")
```

**Why:**
- Internal methods can change freely
- Tests shouldn't break on refactoring
- Test what users see

---

### ❌ Not Using Fixtures

**Bad:**
```python
def test_create_user():
    # Setup duplicated in every test
    db = create_test_db()
    session = db.session()
    service = UserService(session)

    user = service.create_user(email="test@test.com")
    assert user.id is not None

def test_update_user():
    # Same setup again!
    db = create_test_db()
    session = db.session()
    service = UserService(session)
    ...
```

**Good:**
```python
@pytest.fixture
def db_session():
    db = create_test_db()
    session = db.session()
    yield session
    session.close()

@pytest.fixture
def user_service(db_session):
    return UserService(db_session)

def test_create_user(user_service):
    user = user_service.create_user(email="test@test.com")
    assert user.id is not None

def test_update_user(user_service):
    # Clean setup automatically
    ...
```

**Why:**
- DRY (Don't Repeat Yourself)
- Consistent test environment
- Easier to change setup

---

### ❌ Factories Without Factory Libraries

**Bad:**
```python
def test_user_with_articles():
    # Manual object creation
    user = User(id=1, email="test@test.com", username="test")
    db.add(user)

    article1 = Article(id=1, title="Test 1", author_id=user.id)
    article2 = Article(id=2, title="Test 2", author_id=user.id)
    db.add(article1)
    db.add(article2)
    db.commit()

    # Actual test buried in setup
    assert len(user.articles) == 2
```

**Good:**
```python
from factory import Factory, Sequence, SubFactory

class UserFactory(SQLAlchemyModelFactory):
    class Meta:
        model = User

    id = Sequence(lambda n: n)
    email = Sequence(lambda n: f"user{n}@test.com")

class ArticleFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Article

    author = SubFactory(UserFactory)

def test_user_with_articles():
    # Clean, readable test
    user = UserFactory()
    ArticleFactory.create_batch(2, author=user)

    assert len(user.articles) == 2
```

**Why:**
- Less boilerplate
- Easier to read
- Easier to maintain
- Default values handled automatically

---

## Performance Anti-Patterns

### ❌ Loading All Data Then Filtering

**Bad:**
```python
# Load ALL users into memory
all_users = db.query(User).all()

# Filter in Python
active_users = [u for u in all_users if u.is_active]
```

**Good:**
```python
# Filter in database
active_users = db.query(User).filter_by(is_active=True).all()
```

**Why:**
- Database is optimized for filtering
- Don't load unnecessary data
- Use indexes

---

### ❌ Not Using Bulk Operations

**Bad:**
```python
# Slow: N individual inserts
for user_data in user_list:
    user = User(**user_data)
    db.add(user)
    db.commit()  # Commit each one!
```

**Good:**
```python
# Fast: Bulk insert
users = [User(**data) for data in user_list]
db.bulk_save_objects(users)
db.commit()  # One commit
```

**Why:**
- Fewer database round trips
- Much faster for large datasets
- Better transaction handling

---

### ❌ Recreating Services/Connections

**Bad:**
```python
@router.get("/users/{id}")
def get_user(id: int):
    # Creates new service every request
    service = UserService(create_db_connection())
    return service.get_user(id)
```

**Good:**
```python
@router.get("/users/{id}")
def get_user(id: int, service: UserService = Depends(get_user_service)):
    # Service created by dependency injection, pooled
    return service.get_user(id)
```

**Why:**
- Connection pooling
- Resource reuse
- Better performance

---

## Security Anti-Patterns

### ❌ Trusting User Input

**Bad:**
```python
# SQL Injection vulnerability
query = f"SELECT * FROM users WHERE username = '{username}'"
db.execute(query)

# Code Injection vulnerability
exec(user_provided_code)

# Command Injection vulnerability
os.system(f"convert {user_filename} output.png")
```

**Good:**
```python
# Parameterized query
db.query(User).filter_by(username=username).first()

# Sandboxed execution (see masterclass for details)
exec_with_sandbox(user_provided_code)

# Validated input
from pathlib import Path
safe_path = Path(user_filename).resolve()
if not safe_path.is_relative_to(UPLOAD_DIR):
    raise ValueError("Invalid path")
```

**Why:**
- Prevent injection attacks
- Validate all user input
- Use parameterized queries

---

### ❌ Storing Secrets in Code

**Bad:**
```python
# Hardcoded secrets
API_KEY = "sk-1234567890abcdef"
DATABASE_URL = "postgresql://user:password@localhost/db"
```

**Good:**
```python
# Environment variables
import os

API_KEY = os.environ["API_KEY"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Or use python-dotenv
from dotenv import load_dotenv
load_dotenv()
```

**Why:**
- Keep secrets out of version control
- Different secrets per environment
- Easier rotation

---

### ❌ Not Validating DTOs

**Bad:**
```python
@dataclass
class UserDTO:
    email: str
    age: int

# Accepts anything
dto = UserDTO(email="not-an-email", age=-5)
```

**Good:**
```python
from pydantic import BaseModel, EmailStr, Field

class UserDTO(BaseModel):
    email: EmailStr  # Validated as email
    age: int = Field(ge=0, le=150)  # 0 <= age <= 150

# Raises ValidationError
dto = UserDTO(email="not-an-email", age=-5)
```

**Why:**
- Catch invalid data early
- Explicit validation rules
- Better error messages

---

## Best Practices Summary

### ✅ Architecture
- [ ] Use **service layer** for business logic
- [ ] Use **DTOs** for data transfer between layers
- [ ] Use **TYPE_CHECKING** for circular imports
- [ ] Keep models **thin** (data only)
- [ ] Use **dependency injection** (FastAPI Depends)

### ✅ Database
- [ ] Use **eager loading** to avoid N+1
- [ ] Check **identity map** before querying
- [ ] Use **bulk operations** for large datasets
- [ ] Use **transactions** appropriately
- [ ] Use **indexes** on filtered/joined columns

### ✅ Type System
- [ ] Use `from __future__ import annotations`
- [ ] Use **Protocol** for duck typing, not `Any`
- [ ] Use **Generic** to preserve type information
- [ ] Use **Literal** for string enums
- [ ] Use **TypedDict** for structured dicts

### ✅ Code Generation
- [ ] **Register with linecache** before exec()
- [ ] **Validate** generated code with ast.parse()
- [ ] Use **textwrap.dedent** for readable templates
- [ ] Add **metadata** for better error messages
- [ ] Use **builder pattern** for complex generation

### ✅ Testing
- [ ] Use **fixtures** for setup
- [ ] Use **factories** for test data
- [ ] Test **behavior**, not implementation
- [ ] **Mock** external dependencies
- [ ] Test **edge cases** and errors

### ✅ Performance
- [ ] Use **connection pooling**
- [ ] Use **caching** where appropriate (memoization)
- [ ] **Profile** before optimizing
- [ ] Use **async** for I/O-bound operations
- [ ] Use **bulk operations** for large datasets

### ✅ Security
- [ ] **Validate** all user input
- [ ] Use **parameterized queries**
- [ ] **Never** trust user code without sandboxing
- [ ] Use **environment variables** for secrets
- [ ] Use **Pydantic** for validation
- [ ] **Hash** passwords (bcrypt/argon2)
- [ ] Use **HTTPS** in production

### ✅ Code Quality
- [ ] Follow **PEP 8** style guide
- [ ] Write **docstrings** for public APIs
- [ ] Use **type hints** everywhere
- [ ] Keep functions **small** and **focused**
- [ ] Use **meaningful names**
- [ ] **DRY** - Don't Repeat Yourself
- [ ] **YAGNI** - You Aren't Gonna Need It

---

## Decision Matrix

When should you use each pattern?

| Scenario | Pattern | Why |
|----------|---------|-----|
| Data between layers | **DTO** | Immutability, decoupling |
| Business logic | **Service Layer** | Separation of concerns |
| Reusable model behavior | **Mixins** | Composition over inheritance |
| Class-level computed value | **classproperty** | Clean syntax |
| Extensibility points | **Hook System** | Plugin architecture |
| Circular imports | **TYPE_CHECKING** | Zero runtime cost |
| Polymorphic algorithms | **Strategy/Engine** | Open/closed principle |
| Object creation | **Factory** | Abstraction, flexibility |
| Dynamic code | **Builder** | Composability |
| Generic operations | **Base Service** | DRY, consistency |
| Type-safe containers | **Generic[T]** | Preserve type info |
| Test data | **Factories** | Less boilerplate |

---

## Common Questions

### Q: When should I use a DTO vs returning the ORM model?

**A:** Use DTOs when:
- Crossing boundaries (API → Service → Task)
- Need serialization (JSON, pickle)
- Want immutability
- Don't want ORM behavior (lazy loading)

Return ORM when:
- Staying within same layer
- Need to modify and save
- Want relationship loading

### Q: Should all database access go through services?

**A:** Yes, for:
- Consistency
- Easier testing
- Business logic in one place
- Transaction management

No, for:
- Simple scripts
- Data migrations
- Admin commands

### Q: When should I use TYPE_CHECKING vs runtime imports?

**A:** Use `TYPE_CHECKING` when:
- Would cause circular imports
- Only needed for type hints
- Expensive import (pandas, numpy) only for typing

Use runtime imports when:
- Actually using the class/function
- No circular import issue

### Q: Generic vs Any vs Protocol?

**A:**
- **Generic[T]**: When you want to preserve and return the exact type
- **Protocol**: When you care about behavior (duck typing)
- **Any**: Last resort, when truly unknown (avoid!)

---

## Anti-Pattern Quick Test

Ask yourself:

1. ❓ Is my model doing more than data structure? → Use Service
2. ❓ Am I returning ORM models from API? → Use DTO
3. ❓ Do I have circular imports? → Use TYPE_CHECKING
4. ❓ Am I using `except Exception`? → Catch specific exceptions
5. ❓ Am I using mutable default arguments? → Use None as sentinel
6. ❓ Am I using `Any` for type hints? → Use Protocol or Generic
7. ❓ Am I creating new DB connections per request? → Use dependency injection
8. ❓ Do I have N+1 queries? → Use eager loading
9. ❓ Am I concatenating strings for code? → Use templates
10. ❓ Am I using `exec()` without linecache? → Register with linecache

---

## Final Checklist Before Production

- [ ] All user input validated (Pydantic)
- [ ] No secrets in code (use env vars)
- [ ] Database queries optimized (no N+1)
- [ ] DTOs used for API responses
- [ ] Services handle business logic
- [ ] Tests cover edge cases
- [ ] Type hints on all functions
- [ ] Error handling for all external calls
- [ ] Logging for debugging
- [ ] Monitoring/metrics in place
- [ ] Documentation written
- [ ] Code reviewed

---

**Remember:** If it feels wrong, it probably is. Trust your instincts and consult this guide!
