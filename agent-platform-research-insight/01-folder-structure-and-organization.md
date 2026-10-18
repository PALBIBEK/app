# Folder Structure and Organization Patterns

## Overview

This document outlines the recommended folder structure and organization patterns for a production-grade FastAPI backend application. The structure follows clean architecture principles with clear separation of concerns.

## Root Directory Structure

```
backend-api/
├── api_package/              # Main Python package
│   ├── cli/                  # Command-line interface tools
│   ├── config/               # Configuration management
│   ├── cron/                 # Scheduled job management
│   ├── db/                   # Database layer
│   ├── engines/              # Execution engines
│   ├── exception/            # Exception handling
│   ├── infrastructure/       # Infrastructure utilities
│   ├── libs/                 # Internal libraries
│   ├── market_place/         # Marketplace/plugins
│   ├── prompts/              # AI/LLM prompt templates
│   ├── scripts/              # Utility scripts
│   ├── services/             # Business logic layer
│   ├── tasks/                # Background tasks (Celery)
│   ├── templates/            # Jinja2 templates
│   ├── util/                 # Utility functions
│   └── web/                  # Web/API layer
├── instance/                 # Instance-specific configuration
├── patches/                  # Database patches
├── scripts/                  # Deployment scripts
├── pyproject.toml           # Project configuration
└── README.md                # Project documentation
```

## Detailed Directory Breakdown

### 1. CLI Directory (`cli/`)

**Purpose**: Command-line interface tools for management tasks

**Structure**:
```
cli/
├── __init__.py
├── db.py                    # Database commands (migrate, upgrade, seed)
├── worker.py                # Worker process management
└── shell.py                 # Interactive shell
```

**Usage Examples**:
```bash
# Database operations
python -m app.cli db migrate "Add user table"
python -m app.cli db upgrade
python -m app.cli db seed

# Worker management
python -m app.cli worker start
python -m app.cli worker status

# Interactive shell
python -m app.cli shell
```

**Key Patterns**:
- Click or Typer for CLI framework
- Separate commands into logical groups
- Provide helpful error messages
- Support dry-run mode for destructive operations

### 2. Configuration Directory (`config/`)

**Purpose**: Centralized configuration management

**Structure**:
```
config/
├── __init__.py
├── settings.py              # Base settings
├── development_settings.py  # Development overrides
├── production_settings.py   # Production overrides
├── test_settings.py         # Test environment
└── encrypted_loader.py      # Encrypted config loader
```

**Configuration Hierarchy**:
1. Base settings (defaults)
2. Environment-specific settings
3. Instance configuration file
4. Environment variables

**Example Pattern**:
```python
# settings.py
from dynaconf import LazySettings

settings = LazySettings(
    environments=True,
    envvar_prefix="APP",
    env_switcher="APP_ENV",
    settings_file=[
        "config/settings.py",
        "{CONFIG_DIR}/api_config.py",
    ]
)

# Development settings
DEBUG = True
DATABASE_URI = "sqlite:///dev.db"

# Production settings
DEBUG = False
DATABASE_URI = "postgresql://..."
```

### 3. Cron Directory (`cron/`)

**Purpose**: Scheduled job management

**Structure**:
```
cron/
├── __init__.py
├── scheduler.py             # Cron job scheduler
├── jobs/
│   ├── cleanup_jobs.py      # Data cleanup tasks
│   ├── report_jobs.py       # Report generation
│   └── sync_jobs.py         # Data synchronization
```

**Patterns**:
- Use APScheduler or Celery Beat
- Separate job definitions from scheduler
- Implement job locking for distributed systems
- Log job execution and failures

### 4. Database Directory (`db/`)

**Purpose**: Database layer including models, migrations, and utilities

**Structure**:
```
db/
├── __init__.py
├── async_session.py         # Async session management
├── base.py                  # Base database setup
├── models/                  # ORM models
│   ├── __init__.py
│   ├── base_model.py        # Base model class
│   ├── user.py              # User model
│   ├── role.py              # Role model
│   ├── pipeline.py          # Pipeline model
│   └── mixins/              # Reusable model mixins
│       ├── auditable.py     # Audit fields
│       ├── versionable.py   # Version tracking
│       ├── workflowable.py  # Workflow support
│       ├── simulatable.py   # Simulation support
│       └── with_logs.py     # Change logs
└── migrations/              # Alembic migrations
    ├── versions/            # Migration versions
    ├── env.py               # Migration environment
    └── script.py.mako       # Migration template
```

**Model Organization**:
- One file per model (for complex models)
- Group related models (e.g., `auth_models.py` for User, Role, Permission)
- Use mixins for cross-cutting concerns
- Keep models focused on data structure

**Migration Best Practices**:
- Descriptive migration names
- Test migrations on production-like data
- Provide up and down migrations
- Document breaking changes

### 5. Engines Directory (`engines/`)

**Purpose**: Execution engines for processing workflows

**Structure**:
```
engines/
├── __init__.py
├── base_engine.py           # Base engine interface
├── python_engine.py         # Python execution
├── sql_engine.py            # SQL execution
└── spark_engine.py          # Spark execution
```

**Patterns**:
- Define common interface for all engines
- Implement factory pattern for engine selection
- Handle engine-specific errors gracefully
- Support engine configuration

### 6. Exception Directory (`exception/`)

**Purpose**: Centralized exception handling

**Structure**:
```
exception/
├── __init__.py
├── base.py                  # Base exception classes
├── exception_handler.py     # Global exception handlers
└── error_codes.py           # Error code definitions
```

**Exception Hierarchy**:
```python
AppBaseException
├── ApiException
│   ├── RequestApiException (400)
│   ├── UnauthorizedApiException (401)
│   ├── ForbiddenApiException (403)
│   ├── NotFoundApiException (404)
│   └── InternalApiException (500)
└── TaskException
```

**Best Practices**:
- Create specific exception types for different scenarios
- Include error codes for client handling
- Provide detailed error messages for debugging
- Log exceptions appropriately

### 7. Infrastructure Directory (`infrastructure/`)

**Purpose**: Infrastructure-related utilities and integrations

**Structure**:
```
infrastructure/
├── __init__.py
├── cloud/                   # Cloud provider integrations
│   ├── aws.py               # AWS services
│   ├── azure.py             # Azure services
│   └── gcp.py               # GCP services
├── storage/                 # File storage
│   ├── local.py             # Local filesystem
│   └── s3.py                # S3-compatible storage
└── messaging/               # Message queues
    ├── rabbitmq.py          # RabbitMQ
    └── kafka.py             # Kafka
```

### 8. Libs Directory (`libs/`)

**Purpose**: Internal libraries and reusable components

**Structure**:
```
libs/
├── __init__.py
├── smart_select.py          # Query builder abstraction
├── cache.py                 # Caching utilities
├── validators.py            # Custom validators
└── decorators.py            # Reusable decorators
```

### 9. Services Directory (`services/`)

**Purpose**: Business logic layer

**Structure**:
```
services/
├── __init__.py
├── base_service.py          # Base service class
├── user_service.py          # User business logic
├── auth_service.py          # Authentication logic
├── pipeline_service.py      # Pipeline operations
├── mixins/                  # Service mixins
│   ├── auth_mixin.py        # Authorization checks
│   ├── version_mixin.py     # Version control
│   └── workflow_mixin.py    # Workflow management
├── auth/                    # Auth-specific services
│   ├── jwt_service.py       # JWT token management
│   ├── oauth_service.py     # OAuth flows
│   └── saml_service.py      # SAML integration
└── execution_services/      # Execution-related services
    ├── executor.py          # Execution orchestration
    └── validator.py         # Execution validation
```

**Service Organization Principles**:
- One service per domain entity
- Group related services in subdirectories
- Use mixins for cross-cutting concerns
- Keep services focused on business logic

### 10. Tasks Directory (`tasks/`)

**Purpose**: Celery background tasks

**Structure**:
```
tasks/
├── __init__.py
├── celery_app.py            # Celery application setup
├── user_tasks.py            # User-related tasks
├── report_tasks.py          # Report generation
├── cleanup_tasks.py         # Cleanup tasks
└── notification_tasks.py    # Notification tasks
```

**Task Naming Convention**:
```python
@celery_app.task(name="users.send_welcome_email")
def send_welcome_email(user_id: int):
    pass

@celery_app.task(name="reports.generate_monthly")
def generate_monthly_report(month: str):
    pass
```

### 11. Templates Directory (`templates/`)

**Purpose**: Jinja2 templates for emails, reports, etc.

**Structure**:
```
templates/
├── email/                   # Email templates
│   ├── welcome.html
│   ├── password_reset.html
│   └── notification.html
├── reports/                 # Report templates
│   ├── summary.html
│   └── detailed.html
└── exports/                 # Export templates
    ├── csv_export.jinja2
    └── excel_export.jinja2
```

### 12. Utility Directory (`util/`)

**Purpose**: Common utility functions

**Structure**:
```
util/
├── __init__.py
├── utils.py                 # General utilities
├── db_utils.py              # Database utilities
├── file_utils.py            # File operations
├── date_utils.py            # Date/time helpers
├── validation_utils.py      # Validation helpers
└── task_utils.py            # Task utilities
```

**Organization Principles**:
- Group related utilities
- Keep functions pure when possible
- Avoid circular dependencies
- Document utility functions well

### 13. Web Directory (`web/`)

**Purpose**: Web/API layer (FastAPI routes, schemas, dependencies)

**Structure**:
```
web/
├── __init__.py
├── app.py                   # FastAPI app factory
├── routes.py                # Main router
├── users.py                 # User endpoints
├── auth.py                  # Auth endpoints
├── pipelines.py             # Pipeline endpoints
├── dependencies/            # FastAPI dependencies
│   ├── __init__.py
│   ├── api_auth.py          # Auth dependency
│   ├── db_session.py        # DB session dependency
│   ├── jwt_manager.py       # JWT management
│   └── pagination.py        # Pagination helpers
├── middlewares/             # Custom middleware
│   ├── __init__.py
│   ├── logging_middleware.py
│   ├── auth_middleware.py
│   └── cors_middleware.py
├── mixins/                  # API mixins
│   ├── __init__.py
│   ├── entity_apis.py       # CRUD endpoints
│   ├── versionable_apis.py  # Version endpoints
│   └── workflowable_apis.py # Workflow endpoints
├── schema/                  # Request/Response schemas
│   ├── __init__.py
│   ├── base_schema.py       # Base schemas
│   ├── user_schema.py       # User schemas
│   ├── auth_schema.py       # Auth schemas
│   └── common.py            # Common schemas
└── views/                   # View helpers
    ├── __init__.py
    └── response.py          # Response formatting
```

## File Naming Conventions

### Python Files
- **Models**: Singular, snake_case (e.g., `user.py`, `pipeline.py`)
- **Services**: Singular + `_service` (e.g., `user_service.py`)
- **Routes**: Plural (e.g., `users.py`, `pipelines.py`)
- **Tasks**: Plural + `_tasks` (e.g., `user_tasks.py`)
- **Tests**: `test_` + module name (e.g., `test_user_service.py`)

### Directories
- **Plural for collections**: `models/`, `services/`, `tasks/`
- **Singular for purpose**: `config/`, `util/`, `exception/`

## Import Organization

### Standard Import Order
```python
# 1. Standard library imports
import os
from datetime import datetime
from typing import Optional

# 2. Third-party imports
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from pydantic import BaseModel

# 3. Local application imports
from app.db.models import User
from app.services import UserService
from app.web.dependencies import get_current_user
```

### Avoid Circular Imports
- Use type hints with string literals: `"User"` instead of `User`
- Import at function level for circular dependencies
- Restructure code to remove circular dependencies

## Configuration Files

### Key Configuration Files
```
project/
├── pyproject.toml           # Project metadata, dependencies
├── .env.example             # Environment variable template
├── .gitignore               # Git ignore rules
├── alembic.ini              # Alembic configuration
├── pytest.ini               # Pytest configuration
└── Dockerfile               # Docker image definition
```

### pyproject.toml Structure
```toml
[project]
name = "backend-api"
version = "1.0.0"
dependencies = [
    "fastapi>=0.100.0",
    "sqlalchemy>=2.0.0",
    "pydantic>=2.0.0",
]

[dependency-groups]
dev = ["pytest", "black", "ruff"]
test = ["pytest-cov", "pytest-mock"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = "test_*.py"

[tool.ruff]
line-length = 120
```

## Best Practices

### 1. Package Structure
- Use `__init__.py` to expose public APIs
- Keep `__init__.py` minimal
- Avoid wildcard imports in `__init__.py`

### 2. Module Organization
- One class per file for complex classes
- Group related small classes in one file
- Keep files under 500 lines when possible

### 3. Dependency Management
- Use dependency groups in `pyproject.toml`
- Pin major versions, allow minor/patch updates
- Separate dev, test, and production dependencies

### 4. Documentation
- README.md in each major directory
- Docstrings for all public functions/classes
- Type hints for all function parameters

### 5. Testing
- Mirror source structure in tests directory
- Separate unit, integration, and e2e tests
- Use fixtures for common test setup

## Scaling Considerations

### When to Split
- **Services**: Split when service exceeds 500 lines
- **Models**: One file per model when model > 200 lines
- **Routes**: Split by resource (users, pipelines, etc.)

### Modular Architecture
```
project/
├── modules/
│   ├── users/               # User module
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── routes.py
│   │   └── schemas.py
│   └── pipelines/           # Pipeline module
│       ├── models.py
│       ├── services.py
│       ├── routes.py
│       └── schemas.py
```

## Conclusion

A well-organized folder structure is crucial for:
- Developer onboarding
- Code maintainability
- Scaling the team
- Finding code quickly
- Avoiding circular dependencies

The structure presented here balances organization with pragmatism, suitable for projects of various sizes while maintaining clean architecture principles.
