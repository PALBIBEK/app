# Utilities and Helper Patterns

## Overview

This document covers common utility functions and helper patterns used throughout FastAPI applications, including database utilities, file handling, validation, caching, and more.

## Database Utilities

### Query Builder Utilities

```python
# app/util/db_utils.py
from typing import Any, Dict, List, Optional
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Query

class SmartSelect:
    """
    Smart query builder with filter syntax.

    Supports:
    - Field operators: field__gt, field__lt, field__gte, field__lte, field__ne
    - Relationship traversal: role__name="admin"
    - IN queries: field__in=[1, 2, 3]
    """

    def __init__(self, model):
        self.model = model
        self.stmt = select(model)
        self.filters = []

    def filter_by(self, **kwargs):
        """
        Add filters to query.

        Examples:
            SmartSelect(User).filter_by(is_active=True)
            SmartSelect(User).filter_by(id__gt=10)
            SmartSelect(User).filter_by(role__name="admin")
        """
        for key, value in kwargs.items():
            filter_expr = self._build_filter(key, value)
            if filter_expr is not None:
                self.filters.append(filter_expr)

        return self

    def _build_filter(self, key: str, value: Any):
        """Build filter expression from key and value"""
        parts = key.split("__")

        # Traverse relationships
        if len(parts) > 2:
            # e.g., role__permissions__name
            return self._build_relationship_filter(parts, value)

        # Field with operator
        if len(parts) == 2:
            field_name, operator = parts
            field = getattr(self.model, field_name)

            operators = {
                "gt": lambda f, v: f > v,
                "gte": lambda f, v: f >= v,
                "lt": lambda f, v: f < v,
                "lte": lambda f, v: f <= v,
                "ne": lambda f, v: f != v,
                "in": lambda f, v: f.in_(v),
                "like": lambda f, v: f.like(v),
                "ilike": lambda f, v: f.ilike(v),
                "startswith": lambda f, v: f.like(f"{v}%"),
                "endswith": lambda f, v: f.like(f"%{v}"),
                "contains": lambda f, v: f.like(f"%{v}%"),
            }

            if operator in operators:
                return operators[operator](field, value)

        # Simple equality
        field = getattr(self.model, parts[0])
        return field == value

    def _build_relationship_filter(self, parts: List[str], value: Any):
        """Build filter for relationship traversal"""
        # Navigate through relationships
        # Implementation depends on your relationship structure
        pass

    def order_by(self, *fields):
        """Add ordering to query"""
        for field in fields:
            if field.startswith("-"):
                # Descending order
                field_name = field[1:]
                self.stmt = self.stmt.order_by(
                    getattr(self.model, field_name).desc()
                )
            else:
                # Ascending order
                self.stmt = self.stmt.order_by(
                    getattr(self.model, field)
                )

        return self

    def limit(self, limit: int):
        """Limit results"""
        self.stmt = self.stmt.limit(limit)
        return self

    def offset(self, offset: int):
        """Offset results"""
        self.stmt = self.stmt.offset(offset)
        return self

    def execute(self, session):
        """Execute query"""
        if self.filters:
            self.stmt = self.stmt.where(and_(*self.filters))

        result = session.execute(self.stmt)
        return result.scalars().all()

# Usage
users = SmartSelect(User).filter_by(
    is_active=True,
    created_date__gte=datetime(2024, 1, 1),
    role__name="admin"
).order_by("-created_date").limit(10).execute(session)
```

### Bulk Operations

```python
def bulk_insert(session, model, items: List[Dict]):
    """
    Bulk insert items into database.

    Args:
        session: Database session
        model: SQLAlchemy model
        items: List of dictionaries with item data

    Returns:
        Number of inserted items
    """
    if not items:
        return 0

    session.bulk_insert_mappings(model, items)
    session.commit()

    return len(items)

def bulk_update(session, model, items: List[Dict]):
    """
    Bulk update items in database.

    Args:
        session: Database session
        model: SQLAlchemy model
        items: List of dictionaries with item data (must include 'id')

    Returns:
        Number of updated items
    """
    if not items:
        return 0

    session.bulk_update_mappings(model, items)
    session.commit()

    return len(items)

# Usage
users_data = [
    {"name": "User 1", "email": "user1@example.com"},
    {"name": "User 2", "email": "user2@example.com"},
]

bulk_insert(session, User, users_data)
```

## File Utilities

### File Upload/Download

```python
# app/util/file_utils.py
import os
import mimetypes
from pathlib import Path
from typing import BinaryIO, Optional
from fastapi import UploadFile
from werkzeug.utils import secure_filename

class FileManager:
    """Manage file upload and download"""

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save_upload(
        self,
        file: UploadFile,
        subfolder: Optional[str] = None,
        filename: Optional[str] = None
    ) -> str:
        """
        Save uploaded file.

        Args:
            file: Uploaded file
            subfolder: Optional subfolder
            filename: Optional custom filename

        Returns:
            Relative path to saved file
        """
        # Determine filename
        if filename is None:
            filename = secure_filename(file.filename)

        # Determine save path
        if subfolder:
            save_dir = self.base_path / subfolder
            save_dir.mkdir(parents=True, exist_ok=True)
        else:
            save_dir = self.base_path

        save_path = save_dir / filename

        # Save file
        with open(save_path, "wb") as f:
            contents = await file.read()
            f.write(contents)

        # Return relative path
        return str(save_path.relative_to(self.base_path))

    def get_file_path(self, relative_path: str) -> Path:
        """Get absolute path for relative path"""
        return self.base_path / relative_path

    def delete_file(self, relative_path: str) -> bool:
        """Delete file"""
        file_path = self.get_file_path(relative_path)

        if file_path.exists():
            file_path.unlink()
            return True

        return False

    def get_mime_type(self, filename: str) -> str:
        """Get MIME type for file"""
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"

# Usage
file_manager = FileManager("/app/uploads")

# Save uploaded file
@router.post("/upload")
async def upload_file(file: UploadFile):
    path = await file_manager.save_upload(file, subfolder="documents")
    return {"path": path}

# Download file
@router.get("/download/{path:path}")
async def download_file(path: str):
    file_path = file_manager.get_file_path(path)

    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        file_path,
        media_type=file_manager.get_mime_type(path),
        filename=file_path.name
    )
```

### Image Processing

```python
from PIL import Image
from io import BytesIO

def resize_image(
    image_data: bytes,
    max_width: int,
    max_height: int,
    quality: int = 85
) -> bytes:
    """
    Resize image maintaining aspect ratio.

    Args:
        image_data: Image bytes
        max_width: Maximum width
        max_height: Maximum height
        quality: JPEG quality (1-100)

    Returns:
        Resized image bytes
    """
    img = Image.open(BytesIO(image_data))

    # Calculate new size maintaining aspect ratio
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

    # Save to bytes
    output = BytesIO()
    img.save(output, format="JPEG", quality=quality, optimize=True)

    return output.getvalue()

def create_thumbnail(image_data: bytes, size: int = 200) -> bytes:
    """Create square thumbnail"""
    img = Image.open(BytesIO(image_data))

    # Create square crop
    width, height = img.size
    min_dim = min(width, height)

    left = (width - min_dim) // 2
    top = (height - min_dim) // 2
    right = left + min_dim
    bottom = top + min_dim

    img = img.crop((left, top, right, bottom))

    # Resize
    img.thumbnail((size, size), Image.Resampling.LANCZOS)

    # Save
    output = BytesIO()
    img.save(output, format="JPEG", quality=85)

    return output.getvalue()
```

## Validation Utilities

### Custom Validators

```python
# app/util/validators.py
import re
from typing import Any

def validate_email(email: str) -> bool:
    """Validate email format"""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None

def validate_password_strength(password: str) -> Dict[str, Any]:
    """
    Validate password strength.

    Returns:
        Dict with 'valid' and 'errors' keys
    """
    errors = []

    if len(password) < 8:
        errors.append("Password must be at least 8 characters")

    if not any(c.isupper() for c in password):
        errors.append("Password must contain uppercase letter")

    if not any(c.islower() for c in password):
        errors.append("Password must contain lowercase letter")

    if not any(c.isdigit() for c in password):
        errors.append("Password must contain digit")

    special_chars = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    if not any(c in special_chars for c in password):
        errors.append("Password must contain special character")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "strength": calculate_password_strength(password)
    }

def calculate_password_strength(password: str) -> str:
    """Calculate password strength: weak, medium, strong"""
    score = 0

    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1

    if any(c.isupper() for c in password):
        score += 1
    if any(c.islower() for c in password):
        score += 1
    if any(c.isdigit() for c in password):
        score += 1
    if any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        score += 1

    if score <= 2:
        return "weak"
    elif score <= 4:
        return "medium"
    else:
        return "strong"

def validate_url(url: str) -> bool:
    """Validate URL format"""
    pattern = r"^https?:\/\/(www\.)?[-a-zA-Z0-9@:%._\+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_\+.~#?&//=]*)$"
    return re.match(pattern, url) is not None

def sanitize_filename(filename: str) -> str:
    """Sanitize filename to remove dangerous characters"""
    # Remove path separators
    filename = os.path.basename(filename)

    # Remove or replace dangerous characters
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)

    # Limit length
    name, ext = os.path.splitext(filename)
    if len(name) > 200:
        name = name[:200]

    return name + ext
```

## Date/Time Utilities

```python
# app/util/date_utils.py
from datetime import datetime, timedelta, timezone
from typing import Optional

def get_utc_now() -> datetime:
    """Get current UTC time with timezone info"""
    return datetime.now(timezone.utc)

def to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC"""
    if dt.tzinfo is None:
        # Assume local timezone
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)

def format_datetime(dt: datetime, format: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format datetime as string"""
    return dt.strftime(format)

def parse_datetime(date_string: str, format: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """Parse datetime from string"""
    return datetime.strptime(date_string, format)

def add_days(dt: datetime, days: int) -> datetime:
    """Add days to datetime"""
    return dt + timedelta(days=days)

def add_hours(dt: datetime, hours: int) -> datetime:
    """Add hours to datetime"""
    return dt + timedelta(hours=hours)

def days_between(start: datetime, end: datetime) -> int:
    """Calculate days between two dates"""
    return (end - start).days

def is_expired(dt: datetime) -> bool:
    """Check if datetime is in the past"""
    return dt < get_utc_now()

def time_until(dt: datetime) -> timedelta:
    """Get time until datetime"""
    return dt - get_utc_now()

def human_readable_duration(seconds: int) -> str:
    """Convert seconds to human readable duration"""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h"
    else:
        days = seconds // 86400
        return f"{days}d"
```

## Caching Utilities

### Request-Level Cache

```python
# app/libs/cache.py
from contextvars import ContextVar
from typing import Any, Callable, Optional
from functools import wraps

class RequestCache:
    """
    Request-scoped cache using context variables.
    Cache is automatically cleared at end of request.
    """

    _cache: ContextVar[dict] = ContextVar("request_cache", default={})
    _context: ContextVar[Optional[str]] = ContextVar("cache_context", default=None)

    @classmethod
    def get(cls, key: str, default=None) -> Any:
        """Get value from cache"""
        cache = cls._cache.get()
        return cache.get(key, default)

    @classmethod
    def set(cls, key: str, value: Any):
        """Set value in cache"""
        cache = cls._cache.get()
        cache[key] = value
        cls._cache.set(cache)

    @classmethod
    def delete(cls, key: str):
        """Delete value from cache"""
        cache = cls._cache.get()
        cache.pop(key, None)
        cls._cache.set(cache)

    @classmethod
    def clear(cls):
        """Clear entire cache"""
        cls._cache.set({})

    @classmethod
    def scoped_context(cls):
        """Context manager for scoped cache"""
        class CacheContext:
            def __enter__(self):
                cls.clear()
                return cls

            def __exit__(self, *args):
                cls.clear()

        return CacheContext()

def cached(key_func: Optional[Callable] = None):
    """
    Decorator to cache function results.

    Args:
        key_func: Function to generate cache key from args

    Usage:
        @cached(lambda x: f"user_{x}")
        def get_user(user_id):
            return expensive_operation(user_id)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = f"{func.__name__}_{args}_{kwargs}"

            # Check cache
            cached_value = RequestCache.get(cache_key)
            if cached_value is not None:
                return cached_value

            # Call function
            result = func(*args, **kwargs)

            # Cache result
            RequestCache.set(cache_key, result)

            return result

        return wrapper

    return decorator

# Usage
@cached(key_func=lambda user_id: f"user_{user_id}")
def get_user(user_id: int):
    # Expensive database query
    return UserService.get_one(user_id)
```

### Application-Level Cache

```python
from functools import lru_cache
from typing import Any
import redis

class AppCache:
    """Application-level cache using Redis"""

    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        value = self.redis.get(key)
        if value:
            import json
            return json.loads(value)
        return None

    def set(self, key: str, value: Any, ttl: int = 3600):
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Time to live in seconds
        """
        import json
        self.redis.setex(key, ttl, json.dumps(value))

    def delete(self, key: str):
        """Delete value from cache"""
        self.redis.delete(key)

    def clear_pattern(self, pattern: str):
        """Delete all keys matching pattern"""
        keys = self.redis.keys(pattern)
        if keys:
            self.redis.delete(*keys)

# Usage
cache = AppCache(settings.REDIS_URL)

# Cache with TTL
cache.set("user_123", user_data, ttl=300)  # 5 minutes

# Get from cache
user = cache.get("user_123")

# Clear cache pattern
cache.clear_pattern("user_*")
```

## String Utilities

```python
# app/util/string_utils.py
import re
import hashlib
from typing import Optional

def to_snake_case(text: str) -> str:
    """Convert string to snake_case"""
    text = re.sub(r'(?<!^)(?=[A-Z])', '_', text).lower()
    return text

def to_camel_case(text: str) -> str:
    """Convert string to camelCase"""
    components = text.split('_')
    return components[0] + ''.join(x.title() for x in components[1:])

def to_pascal_case(text: str) -> str:
    """Convert string to PascalCase"""
    return ''.join(x.title() for x in text.split('_'))

def slugify(text: str) -> str:
    """Convert string to URL-friendly slug"""
    # Convert to lowercase
    text = text.lower()

    # Replace spaces and special characters with hyphens
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)

    # Remove leading/trailing hyphens
    text = text.strip('-')

    return text

def truncate(text: str, length: int, suffix: str = "...") -> str:
    """Truncate string to maximum length"""
    if len(text) <= length:
        return text

    return text[:length - len(suffix)] + suffix

def generate_hash(text: str, algorithm: str = "sha256") -> str:
    """Generate hash of string"""
    hash_obj = hashlib.new(algorithm)
    hash_obj.update(text.encode())
    return hash_obj.hexdigest()

def mask_sensitive(text: str, visible_chars: int = 4) -> str:
    """Mask sensitive string (e.g., credit card)"""
    if len(text) <= visible_chars:
        return "*" * len(text)

    return "*" * (len(text) - visible_chars) + text[-visible_chars:]

# Usage
email = "john.doe@example.com"
masked = mask_sensitive(email, visible_chars=10)  # "***********mple.com"
```

## Decorator Utilities

```python
# app/util/decorators.py
from functools import wraps
import time
import logging

logger = logging.getLogger(__name__)

def timeit(func):
    """Decorator to measure function execution time"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        duration = time.time() - start

        logger.info(f"{func.__name__} took {duration:.3f}s")

        return result

    return wrapper

def retry(max_attempts: int = 3, delay: int = 1):
    """Decorator to retry function on failure"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise

                    logger.warning(
                        f"Attempt {attempt + 1} failed for {func.__name__}: {e}"
                    )
                    time.sleep(delay * (2 ** attempt))  # Exponential backoff

        return wrapper

    return decorator

def deprecated(message: str = "This function is deprecated"):
    """Decorator to mark function as deprecated"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import warnings
            warnings.warn(
                f"{func.__name__} is deprecated. {message}",
                category=DeprecationWarning,
                stacklevel=2
            )
            return func(*args, **kwargs)

        return wrapper

    return decorator

# Usage
@timeit
@retry(max_attempts=3, delay=2)
def fetch_external_data():
    # May fail, will retry
    pass

@deprecated("Use new_function() instead")
def old_function():
    pass
```

## Best Practices

### 1. Utility Organization
- Group related utilities in same file
- Use descriptive module names
- Document parameters and return types
- Provide usage examples

### 2. Error Handling
- Validate inputs
- Provide meaningful error messages
- Handle edge cases
- Don't silently fail

### 3. Performance
- Cache expensive operations
- Use appropriate data structures
- Avoid unnecessary computations
- Profile before optimizing

### 4. Reusability
- Make utilities generic
- Avoid hard-coded values
- Use dependency injection
- Keep functions pure when possible

## Conclusion

Well-organized utilities and helpers improve:
- Code reusability
- Development speed
- Code consistency
- Maintainability

The patterns shown here provide a comprehensive toolkit for common operations in FastAPI applications.
