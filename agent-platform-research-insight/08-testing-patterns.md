# Testing Patterns for FastAPI Applications

## Overview

This document covers comprehensive testing strategies for FastAPI applications, including unit tests, integration tests, API tests, and testing best practices.

## Testing Stack

### Dependencies

```toml
# pyproject.toml
[dependency-groups]
test = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",      # Async test support
    "pytest-cov>=4.0.0",            # Code coverage
    "pytest-mock>=3.10.0",          # Mocking
    "pytest-env>=0.8.0",            # Environment variables
    "httpx>=0.24.0",                # HTTP client for API tests
    "faker>=18.0.0",                # Test data generation
    "factory-boy>=3.2.0",           # Model factories
]
```

### Pytest Configuration

```ini
# pytest.ini
[pytest]
minversion = 7.0
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Async support
asyncio_mode = auto

# Coverage
addopts =
    --cov=app
    --cov-report=html
    --cov-report=term-missing
    --cov-fail-under=80
    -v
    -s

# Environment variables for tests
env =
    APP_ENV=test
    DATABASE_URI=sqlite:///test.db
    SECRET_KEY=test-secret-key
```

## Test Structure

### Directory Organization

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests
│   ├── test_services/
│   │   ├── test_user_service.py
│   │   └── test_pipeline_service.py
│   └── test_utils/
│       └── test_validators.py
├── integration/             # Integration tests
│   ├── test_database.py
│   └── test_external_apis.py
├── api/                     # API endpoint tests
│   ├── test_auth_routes.py
│   ├── test_user_routes.py
│   └── test_pipeline_routes.py
└── fixtures/                # Test data
    ├── users.json
    └── pipelines.json
```

## Fixtures

### Basic Fixtures

```python
# tests/conftest.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.factory import get_fastapi_app
from app.db.models.base_model import SqlaBase
from app.db.async_session import SQLAlchemyAsync

@pytest.fixture(scope="session")
def test_db_engine():
    """Create test database engine"""
    engine = create_engine(
        "sqlite:///test.db",
        connect_args={"check_same_thread": False}
    )

    # Create tables
    SqlaBase.metadata.create_all(bind=engine)

    yield engine

    # Cleanup
    SqlaBase.metadata.drop_all(bind=engine)
    engine.dispose()

@pytest.fixture(scope="function")
def db_session(test_db_engine):
    """Create database session for each test"""
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_db_engine
    )

    session = TestingSessionLocal()

    yield session

    session.rollback()
    session.close()

@pytest.fixture(scope="function")
def client(db_session):
    """Create test client with database session override"""
    app = get_fastapi_app()

    # Override database dependency
    def override_get_db():
        yield db_session

    from app.web.dependencies.db_session import get_db_session
    app.dependency_overrides[get_db_session] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
```

### Model Factories

```python
# tests/factories.py
import factory
from factory.faker import Faker
from app.db.models.user import User
from app.db.models.pipeline import Pipeline

class UserFactory(factory.Factory):
    """Factory for creating test users"""

    class Meta:
        model = User

    name = Faker("name")
    email = Faker("email")
    password_hash = "hashed_password"
    is_active = True
    is_admin = False

class AdminUserFactory(UserFactory):
    """Factory for admin users"""

    is_admin = True

class PipelineFactory(factory.Factory):
    """Factory for creating test pipelines"""

    class Meta:
        model = Pipeline

    name = Faker("word")
    description = Faker("sentence")
    steps = factory.LazyFunction(lambda: [
        {"name": "step1", "type": "extract"},
        {"name": "step2", "type": "transform"},
    ])
    created_by = factory.SubFactory(UserFactory)

# Usage
def test_user_creation():
    user = UserFactory.create()
    assert user.email is not None

    admin = AdminUserFactory.create()
    assert admin.is_admin is True
```

## Unit Tests

### Service Testing

```python
# tests/unit/test_services/test_user_service.py
import pytest
from unittest.mock import Mock, patch
from app.services.user_service import UserService
from app.exception.base import RequestApiException
from tests.factories import UserFactory

class TestUserService:
    """Test UserService"""

    def test_create_user(self, db_session):
        """Test user creation"""
        user = UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        assert user.id is not None
        assert user.name == "John Doe"
        assert user.email == "john@example.com"
        assert user.password_hash != "SecurePass123"  # Should be hashed

    def test_create_duplicate_user(self, db_session):
        """Test creating user with duplicate email fails"""
        UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        with pytest.raises(RequestApiException) as exc:
            UserService.create_user(
                name="Jane Doe",
                email="john@example.com",  # Duplicate email
                password="SecurePass123",
                role_ids=[]
            )

        assert "already exists" in str(exc.value)

    def test_authenticate_success(self, db_session):
        """Test successful authentication"""
        # Create user
        user = UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        # Authenticate
        authenticated = UserService.authenticate(
            email="john@example.com",
            password="SecurePass123"
        )

        assert authenticated is not None
        assert authenticated.id == user.id

    def test_authenticate_wrong_password(self, db_session):
        """Test authentication with wrong password"""
        UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        authenticated = UserService.authenticate(
            email="john@example.com",
            password="WrongPassword"
        )

        assert authenticated is None

    @patch("app.services.user_service.send_email")
    def test_send_welcome_email(self, mock_send_email, db_session):
        """Test sending welcome email"""
        user = UserFactory.create()

        UserService.send_welcome_email(user.id)

        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args

        assert call_args[1]["to"] == user.email
        assert "welcome" in call_args[1]["subject"].lower()
```

### Async Service Testing

```python
import pytest
import pytest_asyncio

class TestAsyncUserService:
    """Test async methods in UserService"""

    @pytest.mark.asyncio
    async def test_async_get_user(self, db_session):
        """Test async user retrieval"""
        user = UserFactory.create()

        retrieved = await UserService.aget_one(user.id)

        assert retrieved.id == user.id
        assert retrieved.email == user.email

    @pytest.mark.asyncio
    async def test_async_list_users(self, db_session):
        """Test async user listing"""
        # Create multiple users
        users = [UserFactory.create() for _ in range(5)]

        result = await UserService.aget_all(skip=0, limit=10)

        assert len(result) == 5
```

## Integration Tests

### Database Integration

```python
# tests/integration/test_database.py
import pytest
from app.db.models.user import User
from app.db.models.role import Role
from tests.factories import UserFactory, RoleFactory

class TestDatabaseIntegration:
    """Test database operations"""

    def test_user_role_relationship(self, db_session):
        """Test many-to-many relationship between User and Role"""
        user = UserFactory.create()
        role1 = RoleFactory.create(name="admin")
        role2 = RoleFactory.create(name="user")

        # Add roles to user
        user.roles.append(role1)
        user.roles.append(role2)

        db_session.commit()
        db_session.refresh(user)

        # Verify relationships
        assert len(user.roles) == 2
        assert role1 in user.roles
        assert role2 in user.roles

    def test_cascade_delete(self, db_session):
        """Test cascade deletion"""
        user = UserFactory.create()
        pipeline = PipelineFactory.create(created_by=user)

        # Delete user
        db_session.delete(user)
        db_session.commit()

        # Verify pipeline was deleted (if cascade is configured)
        from sqlalchemy import select
        stmt = select(Pipeline).where(Pipeline.id == pipeline.id)
        result = db_session.execute(stmt)
        assert result.scalar_one_or_none() is None
```

## API Tests

### Endpoint Testing

```python
# tests/api/test_user_routes.py
import pytest
from fastapi.testclient import TestClient
from tests.factories import UserFactory

class TestUserRoutes:
    """Test user API endpoints"""

    def test_list_users(self, client, db_session):
        """Test GET /api/users"""
        # Create test users
        users = [UserFactory.create() for _ in range(3)]

        # Make request
        response = client.get("/api/users")

        assert response.status_code == 200
        data = response.json()

        assert len(data) == 3

    def test_get_user(self, client, db_session):
        """Test GET /api/users/{id}"""
        user = UserFactory.create()

        response = client.get(f"/api/users/{user.id}")

        assert response.status_code == 200
        data = response.json()

        assert data["id"] == user.id
        assert data["email"] == user.email

    def test_get_nonexistent_user(self, client):
        """Test GET /api/users/{id} with invalid ID"""
        response = client.get("/api/users/99999")

        assert response.status_code == 404

    def test_create_user(self, client, db_session):
        """Test POST /api/users"""
        user_data = {
            "name": "John Doe",
            "email": "john@example.com",
            "password": "SecurePass123",
        }

        response = client.post("/api/users", json=user_data)

        assert response.status_code == 201
        data = response.json()

        assert data["name"] == user_data["name"]
        assert data["email"] == user_data["email"]
        assert "password" not in data  # Password should not be returned

    def test_create_user_invalid_email(self, client):
        """Test POST /api/users with invalid email"""
        user_data = {
            "name": "John Doe",
            "email": "invalid-email",
            "password": "SecurePass123",
        }

        response = client.post("/api/users", json=user_data)

        assert response.status_code == 422  # Validation error

    def test_update_user(self, client, db_session):
        """Test PATCH /api/users/{id}"""
        user = UserFactory.create()

        update_data = {"name": "Updated Name"}

        response = client.patch(
            f"/api/users/{user.id}",
            json=update_data
        )

        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "Updated Name"

    def test_delete_user(self, client, db_session):
        """Test DELETE /api/users/{id}"""
        user = UserFactory.create()

        response = client.delete(f"/api/users/{user.id}")

        assert response.status_code == 204

        # Verify user is deleted
        response = client.get(f"/api/users/{user.id}")
        assert response.status_code == 404
```

### Authentication Testing

```python
# tests/api/test_auth_routes.py
class TestAuthRoutes:
    """Test authentication endpoints"""

    def test_login_success(self, client, db_session):
        """Test POST /api/auth/login with valid credentials"""
        # Create user
        from app.services.user_service import UserService

        user = UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        # Login
        response = client.post(
            "/api/auth/login",
            json={
                "email": "john@example.com",
                "password": "SecurePass123"
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client, db_session):
        """Test login with wrong password"""
        from app.services.user_service import UserService

        UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        response = client.post(
            "/api/auth/login",
            json={
                "email": "john@example.com",
                "password": "WrongPassword"
            }
        )

        assert response.status_code == 401

    def test_protected_endpoint_without_auth(self, client):
        """Test accessing protected endpoint without authentication"""
        response = client.get("/api/users/me")

        assert response.status_code == 401

    def test_protected_endpoint_with_auth(self, client, db_session):
        """Test accessing protected endpoint with authentication"""
        # Login to get token
        from app.services.user_service import UserService

        user = UserService.create_user(
            name="John Doe",
            email="john@example.com",
            password="SecurePass123",
            role_ids=[]
        )

        response = client.post(
            "/api/auth/login",
            json={
                "email": "john@example.com",
                "password": "SecurePass123"
            }
        )

        token = response.json()["access_token"]

        # Access protected endpoint
        response = client.get(
            "/api/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        data = response.json()

        assert data["email"] == user.email
```

## Mocking

### External API Mocking

```python
from unittest.mock import Mock, patch
import pytest

class TestExternalIntegration:
    """Test external API integrations"""

    @patch("app.services.external_service.requests.get")
    def test_fetch_external_data(self, mock_get):
        """Test fetching data from external API"""
        # Setup mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        # Call service
        from app.services.external_service import ExternalService

        result = ExternalService.fetch_data()

        # Verify
        assert result == {"data": "test"}
        mock_get.assert_called_once()

    @patch("app.services.external_service.requests.post")
    def test_external_api_error(self, mock_post):
        """Test handling external API errors"""
        # Setup mock to raise exception
        mock_post.side_effect = Exception("API Error")

        from app.services.external_service import ExternalService

        with pytest.raises(Exception) as exc:
            ExternalService.post_data({"test": "data"})

        assert "API Error" in str(exc.value)
```

### Database Mocking

```python
@patch("app.services.user_service.SQLAlchemyAsync.async_session")
def test_user_service_with_mock_db(mock_session):
    """Test service with mocked database"""
    # Setup mock
    mock_db = Mock()
    mock_session.return_value = mock_db

    mock_user = Mock()
    mock_user.id = 1
    mock_user.name = "John Doe"

    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_user

    # Call service
    user = UserService.get_one(1)

    # Verify
    assert user.id == 1
    assert user.name == "John Doe"
```

## Test Utilities

### Custom Assertions

```python
# tests/utils.py
def assert_valid_response(response, expected_status=200):
    """Assert response is valid"""
    assert response.status_code == expected_status

    if expected_status < 400:
        assert "error" not in response.json()

def assert_valid_user(user_data):
    """Assert user data is valid"""
    assert "id" in user_data
    assert "email" in user_data
    assert "name" in user_data
    assert "password" not in user_data  # Never return password
```

### Test Data Generators

```python
# tests/generators.py
from faker import Faker

fake = Faker()

def generate_user_data():
    """Generate random user data"""
    return {
        "name": fake.name(),
        "email": fake.email(),
        "password": fake.password(length=12),
    }

def generate_pipeline_data():
    """Generate random pipeline data"""
    return {
        "name": fake.word(),
        "description": fake.sentence(),
        "steps": [
            {"name": f"step_{i}", "type": "extract"}
            for i in range(3)
        ]
    }
```

## Best Practices

### 1. Test Independence
```python
# Each test should be independent
def test_feature_a(db_session):
    # Setup
    user = UserFactory.create()

    # Test
    result = UserService.some_operation(user.id)

    # Verify
    assert result is True

    # Cleanup happens automatically via fixtures
```

### 2. AAA Pattern (Arrange-Act-Assert)
```python
def test_user_creation():
    # Arrange
    user_data = generate_user_data()

    # Act
    user = UserService.create_user(**user_data)

    # Assert
    assert user.id is not None
    assert user.email == user_data["email"]
```

### 3. Test Naming
```python
# GOOD: Descriptive test names
def test_create_user_with_duplicate_email_raises_exception():
    pass

def test_authenticate_with_valid_credentials_returns_user():
    pass

# BAD: Vague test names
def test_user():
    pass

def test_auth():
    pass
```

### 4. Parametrized Tests
```python
@pytest.mark.parametrize("email,expected", [
    ("valid@example.com", True),
    ("invalid-email", False),
    ("@example.com", False),
    ("user@", False),
])
def test_email_validation(email, expected):
    """Test email validation with multiple inputs"""
    result = validate_email(email)
    assert result == expected
```

## Coverage

### Running Tests with Coverage

```bash
# Run all tests with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/unit/test_services/test_user_service.py

# Run with verbose output
pytest -v

# Run with print statements
pytest -s

# Run tests matching pattern
pytest -k "test_user"

# Run failed tests only
pytest --lf
```

### Coverage Report

```bash
# Generate HTML coverage report
pytest --cov=app --cov-report=html

# View in browser
open htmlcov/index.html

# Generate terminal report
pytest --cov=app --cov-report=term-missing
```

## Conclusion

Comprehensive testing ensures:
- Code quality
- Regression prevention
- Documentation through tests
- Confidence in refactoring

The patterns shown here provide a solid foundation for testing FastAPI applications at all levels.
