# Advanced Python Patterns Masterclass
## Lessons from Corridor Platform for Prime Agent Development

**Author**: Extracted from Corridor Platform Analysis
**Date**: 2026-08-27
**Purpose**: Reference guide for building production-grade Python applications

---

## Table of Contents

1. [Custom Descriptors & Metaprogramming](#1-custom-descriptors--metaprogramming)
2. [Strategic Type System Usage](#2-strategic-type-system-usage)
3. [Dynamic Code Generation Architecture](#3-dynamic-code-generation-architecture)
4. [Composition-Based Model System](#4-composition-based-model-system)
5. [Engine Pattern for Extensibility](#5-engine-pattern-for-extensibility)
6. [Factory Pattern with Polymorphism](#6-factory-pattern-with-polymorphism)
7. [DTO Pattern for Data Transfer](#7-dto-pattern-for-data-transfer)
8. [Hook System for Extensibility](#8-hook-system-for-extensibility)
9. [Advanced ORM Patterns](#9-advanced-orm-patterns)
10. [Error Handling & Traceability](#10-error-handling--traceability)
11. [Service Layer Architecture](#11-service-layer-architecture)
12. [Testing Patterns](#12-testing-patterns)

---

## 1. Custom Descriptors & Metaprogramming

### 1.1 The `classproperty` Pattern

**Problem**: Python has `@property` for instances and `@classmethod` for classes, but no built-in way to create computed properties at the class level.

**Solution**: Implement a custom descriptor using Python's descriptor protocol.

```python
import functools
import typing as t

_T = t.TypeVar("_T")
_R_co = t.TypeVar("_R_co", covariant=True)

class classproperty(property, t.Generic[_R_co]):
    """
    Create a property that is accessible using the class directly.
    Similar to classmethods - but for properties.

    Example:
        class Foo:
            _var = 1
            @classproperty
            def var(cls):
                return cls._var

        >>> Foo.var  # No () needed
        1
    """

    def __init__(self, func: t.Callable[[type[_T]], _R_co], /) -> None:
        self.func = func
        functools.update_wrapper(self, func)

    def __get__(self, obj: _T | None, owner: type[_T] | None = None, /) -> _R_co:
        if owner is None and obj is not None:
            owner = type(obj)
        return self.func(owner)
```

**When to Use**:
- ✅ Class-level configuration that shouldn't be instantiated
- ✅ Computed properties based on class attributes
- ✅ Feature flags or capabilities detection
- ❌ Don't use if a simple class attribute suffices

**Real-World Example**:
```python
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class BaseModel(Base):
    __abstract__ = True

    @classproperty
    def is_traceable(cls):
        """Check if model has versioning enabled"""
        return hasattr(cls, "__versioned__")

    @classproperty
    def table_name(cls):
        """Get table name with schema prefix"""
        schema = getattr(cls, "__table_args__", {}).get("schema", "public")
        return f"{schema}.{cls.__tablename__}"

# Usage
if User.is_traceable:
    enable_audit_logging()

print(User.table_name)  # "public.users"
```

**Key Learnings**:
1. ✅ Use **covariant TypeVar** (`_R_co`) for return types
2. ✅ Always call `functools.update_wrapper()` to preserve `__name__`, `__doc__`
3. ✅ Handle both instance (`obj`) and class (`owner`) access
4. ✅ Use `/` (positional-only marker) to prevent keyword argument misuse

---

### 1.2 The `memoized_classproperty` Pattern

**Problem**: Some class-level properties are expensive to compute and should be cached.

**Solution**: Extend `classproperty` with memoization at the class level.

```python
class memoized_classproperty(classproperty, t.Generic[_R_co]):
    """
    Cached class property - computes once per class.

    Example:
        class APIClient:
            @memoized_classproperty
            def schema(cls):
                # Expensive: parse OpenAPI spec
                return parse_openapi_spec()
    """

    def __get__(self, obj: _T | None, owner: type[_T] | None = None, /) -> _R_co:
        if owner is None and obj is not None:
            owner = type(obj)

        func_name = getattr(self.func, "__name__", None)
        assert func_name is not None, "Function must have a __name__ attribute"

        # Use name mangling to avoid collisions
        attr_name = f"_{func_name}__memoized_classproperty"

        # Check __dict__ directly to avoid MRO issues
        if attr_name not in owner.__dict__:
            setattr(owner, attr_name, super().__get__(obj, owner))

        return getattr(owner, attr_name)
```

**When to Use**:
- ✅ Expensive computations (parsing, network calls, reflection)
- ✅ Values that don't change for the class lifetime
- ✅ Lazy initialization of class-level resources
- ❌ Don't use if value needs to update dynamically

**Real-World Example**:
```python
class Agent:
    @memoized_classproperty
    def available_tools(cls):
        """Scan and load all tool modules (expensive)"""
        tools = {}
        for file in Path("tools/").glob("*.py"):
            module = importlib.import_module(f"tools.{file.stem}")
            tools.update(discover_tools(module))
        return tools

    @memoized_classproperty
    def llm_schema(cls):
        """Generate JSON schema for LLM (expensive)"""
        return {
            "type": "object",
            "properties": {
                tool_name: tool.schema
                for tool_name, tool in cls.available_tools.items()
            }
        }

# First access: expensive computation
schema = Agent.llm_schema  # Takes 500ms

# Subsequent accesses: instant (cached)
schema2 = Agent.llm_schema  # <1ms
```

**Key Learnings**:
1. ✅ Use **name mangling** (`__memoized_classproperty`) to prevent attribute collisions
2. ✅ Check `owner.__dict__` directly, not `hasattr()` (avoids MRO lookup)
3. ✅ Thread-safe because class creation happens once at import time
4. ✅ Cached per class, not per instance

---

### 1.3 Hook System via Naming Conventions

**Problem**: Need extensibility points without modifying base classes. Want plugins/extensions to register themselves.

**Solution**: Convention-based hook discovery using introspection.

```python
def run_hooks(
    cls,
    hook_name: str,
    *args,
    exclude_hooks: set[str] | None = None,
    **kwargs
):
    """
    Generic function to run all methods matching pattern `_{hook_name}__*`
    in alphabetical order.

    Args:
        cls: The class or instance to search for hooks
        hook_name: Name of the hook (e.g., "before_save")
        exclude_hooks: Set of method names to skip

    Example:
        class User:
            def _before_save__normalize_email(self):
                self.email = self.email.lower()

            def _before_save__validate_age(self):
                if self.age < 18:
                    raise ValueError("Must be 18+")

        # Runs both hooks in alphabetical order
        run_hooks(user_instance, "before_save")
    """
    exclude_hooks = set(exclude_hooks or [])

    # Find all methods matching the pattern
    hook_funcnames = sorted(
        funcname
        for funcname in dir(cls)
        if funcname.startswith(f"_{hook_name}__")
        and funcname not in exclude_hooks
    )

    # Execute hooks in order
    for funcname in hook_funcnames:
        getattr(cls, funcname)(*args, **kwargs)
```

**When to Use**:
- ✅ Plugin architectures
- ✅ Lifecycle hooks (before_save, after_create, etc.)
- ✅ Cross-cutting concerns (validation, normalization, logging)
- ✅ When you want multiple extensions without coupling

**Real-World Example**:
```python
from sqlalchemy import event

class BaseModel:
    """Base model with hook support"""
    pass

# Register SQLAlchemy event to trigger hooks
@event.listens_for(BaseModel, "before_insert", propagate=True)
def before_insert_trigger(mapper, connection, target):
    run_hooks(target, "before_insert", mapper, connection)

@event.listens_for(BaseModel, "before_update", propagate=True)
def before_update_trigger(mapper, connection, target):
    run_hooks(target, "before_update", mapper, connection)


# Now any model can add hooks
class User(BaseModel):
    __tablename__ = "users"

    email: str
    username: str
    created_at: datetime

    def _before_insert__set_created_at(self, mapper, connection):
        """Hook: Set creation timestamp"""
        self.created_at = datetime.utcnow()

    def _before_insert__normalize_email(self, mapper, connection):
        """Hook: Lowercase email"""
        self.email = self.email.lower()

    def _before_update__normalize_email(self, mapper, connection):
        """Hook: Lowercase email on updates too"""
        self.email = self.email.lower()

    def _before_update__validate_username(self, mapper, connection):
        """Hook: Ensure valid username"""
        if not self.username.isalnum():
            raise ValueError("Username must be alphanumeric")


# Usage - hooks run automatically
user = User(email="Test@Example.COM", username="john_doe")
session.add(user)
session.commit()  # Hooks run: created_at set, email lowercased
```

**Advantages**:
- ✅ **Decentralized**: Add hooks without modifying base class
- ✅ **Discoverable**: Easy to see all hooks via naming
- ✅ **Ordered**: Alphabetical execution is predictable
- ✅ **Testable**: Can exclude hooks for testing
- ✅ **Self-documenting**: Method name explains when it runs

**Key Learnings**:
1. ✅ Use **double underscore separator** (`_hookname__description`) for clarity
2. ✅ `dir(cls)` returns all attributes including inherited ones
3. ✅ Always provide `exclude_hooks` for testing/debugging
4. ✅ Document hook signatures in base class docstring

---

## 2. Strategic Type System Usage

### 2.1 TYPE_CHECKING for Circular Imports

**Problem**: Circular imports break at runtime. Type checkers need full type information.

**Solution**: Import types only during static analysis using `TYPE_CHECKING`.

```python
import typing as t

# Runtime imports
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column

# Type-only imports (zero runtime cost)
if t.TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from .user import User
    from .organization import Organization

class Project:
    id: Mapped[int]
    owner_id: Mapped[int]

    # Type hints work for type checkers, no circular import at runtime
    def get_owner(self, session: "AsyncSession") -> "User":
        """Type hints use string literals for forward references"""
        return session.get(User, self.owner_id)

    def get_organization(self) -> "Organization | None":
        """Optional with forward reference"""
        return self.organization if hasattr(self, "organization") else None
```

**When to Use**:
- ✅ **Always** for type-only imports that would cause circular imports
- ✅ Protocol definitions
- ✅ Expensive imports only needed for typing (e.g., pandas for type hints)
- ❌ Don't use for runtime-needed imports

**Real-World Example**:
```python
# agent_runtime.py
import typing as t
from dataclasses import dataclass

if t.TYPE_CHECKING:
    from .tools import ToolRegistry
    from .llm import LLMClient
    from .memory import MemoryStore

@dataclass
class AgentConfig:
    name: str
    model: str

    def create_runtime(
        self,
        tools: "ToolRegistry",
        llm: "LLMClient",
        memory: "MemoryStore"
    ) -> "AgentRuntime":
        """All type hints work without circular imports"""
        return AgentRuntime(config=self, tools=tools, llm=llm, memory=memory)


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        tools: "ToolRegistry",
        llm: "LLMClient",
        memory: "MemoryStore"
    ):
        self.config = config
        self.tools = tools
        self.llm = llm
        self.memory = memory
```

**Combined with PEP 563** (postponed annotations):
```python
from __future__ import annotations  # Python 3.7+

import typing as t

if t.TYPE_CHECKING:
    from .models import User, Organization

# No quotes needed with __future__ import
class Service:
    def get_user(self, user_id: int) -> User:
        """Type checker sees User, runtime sees string"""
        pass

    def get_org(self, org_id: int) -> Organization | None:
        """Union types work naturally"""
        pass
```

**Key Learnings**:
1. ✅ Import `typing as t` for brevity (convention)
2. ✅ Use string literals for forward references
3. ✅ Combine with `from __future__ import annotations` in Python 3.7+
4. ✅ Type checkers evaluate `TYPE_CHECKING` as `True`
5. ✅ Runtime evaluates `TYPE_CHECKING` as `False`

---

### 2.2 TypeVars with Variance

**Problem**: Generic types need different variance for inputs vs outputs.

**Solution**: Use covariant TypeVars for return types, contravariant for parameters.

```python
import typing as t

# Covariant: for return types (outputs can be more specific)
_T_co = t.TypeVar("_T_co", covariant=True)

# Contravariant: for parameter types (inputs can be more general)
_T_contra = t.TypeVar("_T_contra", contravariant=True)

# Invariant: default, no variance
_T = t.TypeVar("_T")


class Getter(t.Generic[_T_co]):
    """Returns T_co - covariant is correct"""
    def get(self) -> _T_co:
        ...

class Setter(t.Generic[_T_contra]):
    """Accepts T_contra - contravariant is correct"""
    def set(self, value: _T_contra) -> None:
        ...

class Container(t.Generic[_T]):
    """Both gets and sets - invariant is required"""
    def get(self) -> _T:
        ...
    def set(self, value: _T) -> None:
        ...
```

**Liskov Substitution Principle**:
```python
class Animal: pass
class Dog(Animal): pass
class Poodle(Dog): pass

# Covariant example (return types)
dog_getter: Getter[Dog] = ...
animal_getter: Getter[Animal] = dog_getter  # ✅ OK: Dog is an Animal

# Contravariant example (parameter types)
animal_setter: Setter[Animal] = ...
dog_setter: Setter[Dog] = animal_setter  # ✅ OK: can accept anything Animal accepts
```

**Real-World Example**:
```python
from typing import Protocol, TypeVar

_T_co = TypeVar("_T_co", covariant=True)
_T_contra = TypeVar("_T_contra", contravariant=True)

class Reader(Protocol[_T_co]):
    """Covariant: only returns T_co"""
    def read(self) -> _T_co: ...

class Writer(Protocol[_T_contra]):
    """Contravariant: only accepts T_contra"""
    def write(self, data: _T_contra) -> None: ...

class Storage(Protocol[_T]):
    """Invariant: both reads and writes T"""
    def read(self) -> _T: ...
    def write(self, data: _T) -> None: ...


# Usage
def process_animals(reader: Reader[Animal]) -> None:
    """Accepts any reader that returns Animals or subclasses"""
    animal = reader.read()
    print(animal.name)

dog_reader: Reader[Dog] = ...
process_animals(dog_reader)  # ✅ OK: Dog is subclass of Animal (covariance)


def save_dogs(writer: Writer[Dog]) -> None:
    """Accepts any writer that can handle Dogs or superclasses"""
    dog = Dog("Fido")
    writer.write(dog)

animal_writer: Writer[Animal] = ...
save_dogs(animal_writer)  # ✅ OK: Animal writer can handle Dog (contravariance)
```

**When to Use**:
- **Covariant** (`_co`): Return types, read-only containers
- **Contravariant** (`_contra`): Parameter types, write-only containers
- **Invariant** (default): Read-write containers, mutable structures

**Key Learnings**:
1. ✅ Covariant = "Producer" = Returns values = `_T_co`
2. ✅ Contravariant = "Consumer" = Accepts values = `_T_contra`
3. ✅ Most collections are invariant (list, dict are not covariant in Python)
4. ✅ Protocols can be covariant/contravariant, classes cannot

---

### 2.3 Runtime Type Inspection

**Problem**: Need to inspect generic types at runtime (for validation, serialization).

**Solution**: Use `typing.get_origin()`, `typing.get_args()`, and `inspect.get_annotations()`.

```python
import typing as t
import inspect
from typing import get_origin, get_args

class DataType:
    """Helper for runtime type inspection"""

    @staticmethod
    def get_base_type(annotation: t.Any) -> type | None:
        """Extract base type from annotation"""
        origin = get_origin(annotation)
        if origin is not None:
            return origin
        if isinstance(annotation, type):
            return annotation
        return None

    @staticmethod
    def get_args(annotation: t.Any) -> tuple:
        """Get generic type arguments"""
        return get_args(annotation)

    @staticmethod
    def is_optional(annotation: t.Any) -> bool:
        """Check if type is Optional[T]"""
        origin = get_origin(annotation)
        if origin is t.Union:
            args = get_args(annotation)
            return type(None) in args
        return False

    @staticmethod
    def unwrap_optional(annotation: t.Any) -> t.Any:
        """Convert Optional[T] -> T"""
        if DataType.is_optional(annotation):
            args = get_args(annotation)
            return next(arg for arg in args if arg is not type(None))
        return annotation


# Real-world example: JSON validation
from pydantic import TypeAdapter

def validate_json_field(obj: object, field_name: str) -> None:
    """Validate JSON string field against type annotation"""

    # Get annotations from entire MRO chain
    annotations = {}
    for klass in type(obj).__mro__:
        annotations.update(inspect.get_annotations(klass, eval_str=True))

    if field_name not in annotations:
        return

    annotation = annotations[field_name]

    # Check if it's Annotated[str, JSONString[SomeType]]
    origin = get_origin(annotation)
    if origin is t.Annotated:
        args = get_args(annotation)
        base_type = args[0]  # str
        metadata = args[1:]   # (JSONString[SomeType],)

        for meta in metadata:
            if get_origin(meta) is JSONString:
                inner_type = get_args(meta)[0]

                # Validate with Pydantic
                value = getattr(obj, field_name)
                TypeAdapter(inner_type).validate_json(value, strict=True)


# Usage
from typing import Annotated

class JSONString(t.Generic[_T]):
    """Marker for JSON string fields"""
    pass

class User:
    id: int
    preferences: Annotated[str, JSONString[dict[str, bool]]]

user = User()
user.preferences = '{"dark_mode": true}'
validate_json_field(user, "preferences")  # ✅ Valid

user.preferences = '{"dark_mode": "yes"}'
validate_json_field(user, "preferences")  # ❌ ValidationError
```

**Key Functions**:
```python
# typing module functions
get_origin(list[int])        # list
get_origin(dict[str, int])   # dict
get_origin(Optional[str])    # typing.Union
get_origin(int)              # None

get_args(list[int])          # (int,)
get_args(dict[str, int])     # (str, int)
get_args(Optional[str])      # (str, <class 'NoneType'>)

# inspect module functions
inspect.get_annotations(cls, eval_str=True)  # Resolve string annotations
```

**Key Learnings**:
1. ✅ Use `inspect.get_annotations(eval_str=True)` to resolve string annotations
2. ✅ Walk `__mro__` manually to get inherited annotations
3. ✅ `get_origin()` returns the base generic (list, dict, Union)
4. ✅ `get_args()` returns the type parameters
5. ✅ Check for `type(None)` in Union args to detect Optional

---

## 3. Dynamic Code Generation Architecture

### 3.1 Builder Pattern for Code Assembly

**Problem**: Need to generate Python/SQL/other code dynamically with proper structure, imports, and error handling.

**Solution**: Builder pattern with language-specific backends.

```python
from typing import Protocol
from abc import ABC, abstractmethod

class LanguageSpec(Protocol):
    """Protocol for language-specific code generation"""

    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None
    ) -> str:
        """Generate function definition in target language"""
        ...

    def make_literal(self, value: any, type_hint: str) -> str:
        """Convert Python value to target language literal"""
        ...

    def error_handler(self) -> str:
        """Generate error handling boilerplate"""
        ...


class CodeBuilder:
    """
    Builder for dynamically generating executable code.
    Language-agnostic abstraction.

    Example:
        builder = CodeBuilder(lang_spec="python")
        builder.add_import("pandas as pd")
        builder.add_import("numpy as np")
        builder.define_function(
            name="process_data",
            body="return df.groupby('category').sum()",
            inputs=["df: pd.DataFrame"],
            output_type="pd.DataFrame"
        )
        code = builder.build()
    """

    def __init__(self, lang_spec: LanguageSpec | str):
        self._code_lines: list[str] = []
        self._imports: set[str] = set()
        self._functions: dict[str, str] = {}

        if isinstance(lang_spec, str):
            self.lang = LanguageSpecFactory.create(lang_spec)
        else:
            self.lang = lang_spec

    def add_import(self, import_stmt: str) -> "CodeBuilder":
        """Add import statement (fluent interface)"""
        self._imports.add(import_stmt)
        return self

    def add_line(self, line: str) -> "CodeBuilder":
        """Add code line"""
        self._code_lines.append(line)
        return self

    def add_lines(self, lines: str | list[str]) -> "CodeBuilder":
        """Add multiple lines"""
        if isinstance(lines, str):
            lines = lines.splitlines()
        self._code_lines.extend(lines)
        return self

    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None = None,
        metadata: dict | None = None
    ) -> "CodeBuilder":
        """Define a function"""
        if name in self._functions:
            raise ValueError(f"Function {name} already defined")

        func_code = self.lang.define_function(name, body, inputs, output_type)
        self._functions[name] = func_code
        return self

    def build(self) -> str:
        """Assemble final code"""
        parts = []

        # 1. Imports
        if self._imports:
            parts.append("\n".join(sorted(self._imports)))
            parts.append("")

        # 2. Error handling boilerplate
        parts.append(self.lang.error_handler())
        parts.append("")

        # 3. Functions
        for func_code in self._functions.values():
            parts.append(func_code)
            parts.append("")

        # 4. Additional code
        if self._code_lines:
            parts.append("\n".join(self._code_lines))

        return "\n".join(parts)

    def execute(self, globals_dict: dict | None = None, locals_dict: dict | None = None):
        """Build and execute code"""
        code = self.build()
        exec(compile(code, "<generated>", "exec"), globals_dict or {}, locals_dict or {})
```

**Real-World Example**: Multi-language code generation

```python
class PythonLanguageSpec:
    """Python code generation"""

    def define_function(self, name: str, body: str, inputs: list[str], output_type: str | None) -> str:
        input_str = ", ".join(inputs)
        return_hint = f" -> {output_type}" if output_type else ""

        return f"""def {name}({input_str}){return_hint}:
{self._indent(body, 4)}"""

    def make_literal(self, value: any, type_hint: str) -> str:
        return repr(value)

    def error_handler(self) -> str:
        return """
def _handle_error(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"Error in {func.__name__}: {e}")
            raise
    return wrapper
"""

    def _indent(self, text: str, spaces: int) -> str:
        return "\n".join(" " * spaces + line if line.strip() else "" for line in text.splitlines())


class SQLLanguageSpec:
    """SQL code generation"""

    def define_function(self, name: str, body: str, inputs: list[str], output_type: str | None) -> str:
        input_str = ", ".join(inputs)
        return f"""CREATE OR REPLACE FUNCTION {name}({input_str})
RETURNS {output_type or 'TABLE'}
AS $$
{body}
$$ LANGUAGE plpgsql;"""

    def make_literal(self, value: any, type_hint: str) -> str:
        if isinstance(value, str):
            return f"'{value}'"
        return str(value)


# Usage
builder = CodeBuilder(PythonLanguageSpec())
builder.add_import("import pandas as pd")
builder.add_import("from typing import Dict")

builder.define_function(
    name="aggregate_sales",
    body="""grouped = df.groupby('category')['amount'].sum()
return grouped.to_dict()""",
    inputs=["df: pd.DataFrame"],
    output_type="Dict[str, float]"
)

code = builder.build()
print(code)
```

Output:
```python
import pandas as pd
from typing import Dict

def _handle_error(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(f"Error in {func.__name__}: {e}")
            raise
    return wrapper

def aggregate_sales(df: pd.DataFrame) -> Dict[str, float]:
    grouped = df.groupby('category')['amount'].sum()
    return grouped.to_dict()
```

**Key Learnings**:
1. ✅ **Fluent interface**: Return `self` from builder methods
2. ✅ **Separation of concerns**: Structure (builder) vs syntax (language spec)
3. ✅ **Composition**: Build code incrementally
4. ✅ **Validation**: Check for duplicate function names
5. ✅ **Testability**: Can inspect intermediate state

---

### 3.2 Improved `exec()` with Traceback Support

**Problem**: Dynamic code executed with `exec()` doesn't show source lines in tracebacks.

**Solution**: Register generated code with `linecache` before execution.

```python
import linecache
import uuid
from typing import Any

def exec_with_traceback(
    source_code: str,
    globals_dict: dict[str, Any] | None = None,
    locals_dict: dict[str, Any] | None = None,
    filename: str | None = None
) -> None:
    """
    Execute code with full traceback support.

    Args:
        source_code: Python code to execute
        globals_dict: Global namespace
        locals_dict: Local namespace
        filename: Friendly name for tracebacks

    Example:
        code = '''
        def greet(name):
            print(f"Hello, {name}!")
            raise ValueError("Demo error")

        greet("Alice")
        '''

        exec_with_traceback(code, filename="<user_script>")

        # Traceback shows actual source lines:
        # File "<user_script>", line 3, in greet
        #   raise ValueError("Demo error")
    """
    if filename is None:
        filename = f"<generated-{uuid.uuid4().hex[:8]}>"

    # Register source code in linecache BEFORE compiling
    # This enables tracebacks to show actual source lines
    if filename not in linecache.cache:
        linecache.cache[filename] = (
            len(source_code),           # size
            None,                       # mtime
            source_code.splitlines(keepends=True),  # lines
            filename                    # name
        )

    # Compile with dont_inherit=True for clean namespace
    code_obj = compile(source_code, filename, "exec", dont_inherit=True)

    # Execute
    if globals_dict is None:
        globals_dict = {}
    if locals_dict is None:
        locals_dict = globals_dict

    exec(code_obj, globals_dict, locals_dict)
```

**Advanced: Custom Traceback Formatting**

```python
from types import FrameType
import traceback
import sys

class CodeExecutor:
    """Execute user code with custom error formatting"""

    def __init__(self, metadata: dict[str, str] | None = None):
        self.metadata = metadata or {}

    def execute(
        self,
        source_code: str,
        globals_dict: dict | None = None,
        locals_dict: dict | None = None
    ) -> dict:
        """Execute code and return locals"""
        filename = f"<{self.metadata.get('name', 'script')}>"

        # Add metadata to globals for traceback access
        if globals_dict is None:
            globals_dict = {}
        globals_dict["__executor_metadata__"] = self.metadata

        # Register with linecache
        linecache.cache[filename] = (
            len(source_code),
            None,
            source_code.splitlines(keepends=True),
            filename
        )

        try:
            exec_with_traceback(source_code, globals_dict, locals_dict, filename)
            return locals_dict or globals_dict

        except Exception as e:
            # Custom error formatting
            self._format_error(e, filename)
            raise

    def _format_error(self, exc: Exception, filename: str) -> None:
        """Format error with context"""
        tb = traceback.extract_tb(exc.__traceback__)

        print(f"\n{'='*70}")
        print(f"Error in {self.metadata.get('name', 'generated code')}")
        if "version" in self.metadata:
            print(f"Version: {self.metadata['version']}")
        print(f"{'='*70}\n")

        for frame in tb:
            if frame.filename == filename:
                print(f"  Line {frame.lineno} in {frame.name}")
                print(f"    {frame.line}")

        print(f"\n{type(exc).__name__}: {exc}")
        print(f"{'='*70}\n")


# Usage
executor = CodeExecutor(metadata={
    "name": "Sales Report Generator",
    "version": "v2.3",
    "author": "Analytics Team"
})

code = """
def calculate_revenue(sales):
    total = sum(s['amount'] for s in sales)
    return total / 0  # Bug!

sales_data = [{'amount': 100}, {'amount': 200}]
result = calculate_revenue(sales_data)
"""

try:
    executor.execute(code)
except Exception:
    pass  # Error already formatted
```

Output:
```
======================================================================
Error in Sales Report Generator
Version: v2.3
======================================================================

  Line 3 in calculate_revenue
    return total / 0  # Bug!

ZeroDivisionError: division by zero
======================================================================
```

**Key Learnings**:
1. ✅ **Always** register dynamic code with `linecache` before `exec()`
2. ✅ Use `dont_inherit=True` in `compile()` for clean namespace
3. ✅ Store metadata in closure for custom error messages
4. ✅ Format: `(size, mtime, lines, filename)` tuple for linecache
5. ✅ Use unique filenames to avoid collisions

---

## 4. Composition-Based Model System

### 4.1 Mixin Pattern for Reusable Behaviors

**Problem**: Need to add capabilities to models without deep inheritance hierarchies.

**Solution**: Small, focused mixins that provide specific features.

```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.orm import relationship

Base = declarative_base()

# ============== Mixins ==============

class TimestampMixin:
    """Adds created_at and updated_at timestamps"""

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AuditMixin:
    """Adds audit trail fields"""

    @declared_attr
    def created_by_id(cls):
        return Column(Integer, ForeignKey("users.id"))

    @declared_attr
    def updated_by_id(cls):
        return Column(Integer, ForeignKey("users.id"))

    @declared_attr
    def created_by(cls):
        return relationship("User", foreign_keys=[cls.created_by_id])

    @declared_attr
    def updated_by(cls):
        return relationship("User", foreign_keys=[cls.updated_by_id])


class SoftDeleteMixin:
    """Adds soft delete capability"""

    deleted_at = Column(DateTime, nullable=True)
    deleted_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, user_id: int) -> None:
        self.deleted_at = datetime.utcnow()
        self.deleted_by_id = user_id


class SlugMixin:
    """Adds URL-friendly slug generation"""

    slug = Column(String(255), unique=True, index=True)

    def generate_slug(self) -> str:
        import re
        slug = re.sub(r'[^\w\s-]', '', self.name.lower())
        slug = re.sub(r'[-\s]+', '-', slug)
        self.slug = slug
        return slug


# ============== Models ==============

class BlogPost(Base, TimestampMixin, AuditMixin, SoftDeleteMixin, SlugMixin):
    """
    BlogPost has:
    - created_at, updated_at (TimestampMixin)
    - created_by, updated_by (AuditMixin)
    - soft delete (SoftDeleteMixin)
    - slug generation (SlugMixin)
    """
    __tablename__ = "blog_posts"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    content = Column(String, nullable=False)


class Product(Base, TimestampMixin, SlugMixin):
    """
    Product has only:
    - created_at, updated_at (TimestampMixin)
    - slug generation (SlugMixin)
    """
    __tablename__ = "products"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    price = Column(Integer, nullable=False)


# Usage
post = BlogPost(name="My First Post", content="Hello World")
post.generate_slug()  # from SlugMixin
# post.created_at automatically set by TimestampMixin
# post.created_by can be set via AuditMixin
# post.soft_delete(user_id=1) available from SoftDeleteMixin
```

**Naming Convention for Mixins**:
- `*Mixin` - Provides reusable behavior
- `*able` - Indicates capability (e.g., `Versionable`, `Searchable`)

**Key Learnings**:
1. ✅ Keep mixins **focused** - single responsibility
2. ✅ Use `@declared_attr` for SQLAlchemy relationships/columns that reference class
3. ✅ Mixins should be **stateless** - no `__init__`
4. ✅ Order matters: `Base` first, then mixins
5. ✅ Test mixins independently

---

### 4.2 Dynamic Class Generation with `@declared_attr`

**Problem**: Need to create related classes automatically (e.g., versioning).

**Solution**: Use `@declared_attr` to dynamically create inner classes.

```python
from sqlalchemy import Integer, BigInteger, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship, Mapped, mapped_column

class Versionable:
    """
    Adds versioning capability to models.

    Usage:
        class Pipeline(Base, Versionable):
            __tablename__ = "pipelines"

            class VersionCustomInfo:
                # Extra fields for PipelineVersion
                status = Column(String(50))
    """

    @declared_attr
    def Version(cls):
        """Dynamically create {Model}Version class"""
        parent_cls = cls
        entity_name = parent_cls.__name__
        entity_table = parent_cls.__tablename__

        # Ensure model defines custom fields
        assert hasattr(parent_cls, "VersionCustomInfo"), (
            f"Must define {entity_name}.VersionCustomInfo"
        )

        # Create version class at runtime
        class VersionInner(Base, TimestampMixin, parent_cls.VersionCustomInfo):
            # Set metadata for SQLAlchemy
            __clsname__ = f"{entity_name}Version"
            __tablename__ = f"{entity_table}_version"

            __table_args__ = (
                UniqueConstraint("parent_id", "version"),
            )

            # Primary key
            id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

            # Version number
            version: Mapped[int] = mapped_column(Integer, default=1)

            # Foreign key to parent
            parent_id: Mapped[int] = mapped_column(
                BigInteger,
                ForeignKey(f"{entity_table}.id"),
                index=True
            )

            # Relationship to parent
            parent = relationship(
                parent_cls,
                back_populates="versions",
                uselist=False
            )

            def __init__(self, *args, **kwargs):
                # Auto-create parent if not provided
                if "parent" not in kwargs and "parent_id" not in kwargs:
                    kwargs["parent"] = parent_cls()
                super().__init__(*args, **kwargs)

        return VersionInner

    @declared_attr
    def versions(cls):
        """Relationship from parent to versions"""
        return relationship(cls.Version, back_populates="parent")


# Usage
class Pipeline(Base, Versionable):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))

    class VersionCustomInfo:
        """Custom fields for PipelineVersion"""
        status: Mapped[str] = mapped_column(String(50), default="draft")
        config: Mapped[str] = mapped_column(String)


# Now Pipeline.Version exists automatically!
pipeline = Pipeline(name="ETL Pipeline")
v1 = Pipeline.Version(parent=pipeline, version=1, config='{"key": "value"}')

# Type-safe relationships
assert v1.parent == pipeline
assert v1 in pipeline.versions
```

**How It Works**:
1. `@declared_attr` runs **after** class is created
2. Creates `VersionInner` class with closure over `parent_cls`
3. SQLAlchemy registers `VersionInner` as `PipelineVersion`
4. Bidirectional relationships automatically work

**Key Learnings**:
1. ✅ Use `@declared_attr` for dynamic attributes that reference the class
2. ✅ Closures capture `parent_cls` for relationships
3. ✅ Set `__clsname__` for SQLAlchemy string-based relationships
4. ✅ Use `assert` to enforce required nested classes
5. ✅ This pattern enables zero-boilerplate versioning

---

## 5. Engine Pattern for Extensibility

### 5.1 Strategy Pattern for Code Generation

**Problem**: Different entity types (functions, pipelines, prompts) need different code generation logic.

**Solution**: Strategy pattern - each entity type gets its own "engine".

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

# ============== DTOs ==============

@dataclass(frozen=True)
class GlobalFunctionDTO:
    id: int
    name: str
    definition: str  # User's Python code
    input_params: list[dict]
    output_type: str | None


@dataclass(frozen=True)
class PromptDTO:
    id: int
    name: str
    template: str  # Jinja2 template
    model: str
    temperature: float


@dataclass(frozen=True)
class PipelineDTO:
    id: int
    name: str
    steps: list[dict]
    parallelism: int


# ============== Engine Protocol ==============

class CodeEngine(Protocol):
    """Protocol that all engines must implement"""

    def generate(self, builder: CodeBuilder, dto: Any) -> CodeBuilder:
        """
        Generate code for this entity type.

        Args:
            builder: CodeBuilder to add code to
            dto: Entity-specific DTO

        Returns:
            Modified builder (for chaining)
        """
        ...


# ============== Concrete Engines ==============

class GlobalFunctionEngine:
    """Generates code for user-defined functions"""

    def generate(self, builder: CodeBuilder, dto: GlobalFunctionDTO) -> CodeBuilder:
        # 1. Handle optional parameters with defaults
        optional_params = [
            {
                "name": param["name"],
                "default": param["default_value"],
                "type": param["type"]
            }
            for param in dto.input_params
            if not param["is_required"]
        ]

        # 2. Generate function
        builder.define_function(
            name=f"global_function_{dto.id}",
            body=dto.definition,  # User's code
            inputs=[p["name"] for p in dto.input_params],
            output_type=dto.output_type,
            defaults=optional_params,
            metadata={
                "type": "GlobalFunction",
                "name": dto.name,
                "id": dto.id
            }
        )

        return builder


class PromptEngine:
    """Generates code for LLM prompts"""

    def generate(self, builder: CodeBuilder, dto: PromptDTO) -> CodeBuilder:
        # Import LLM client
        builder.add_import("from openai import OpenAI")

        # Generate prompt function
        code = f'''
client = OpenAI()

def prompt_{dto.id}(**kwargs):
    """Execute prompt: {dto.name}"""
    from jinja2 import Template

    template = Template({dto.template!r})
    rendered = template.render(**kwargs)

    response = client.chat.completions.create(
        model={dto.model!r},
        messages=[{{"role": "user", "content": rendered}}],
        temperature={dto.temperature}
    )

    return response.choices[0].message.content
'''

        builder.add_lines(code)
        return builder


class PipelineEngine:
    """Generates code for data pipelines"""

    def generate(self, builder: CodeBuilder, dto: PipelineDTO) -> CodeBuilder:
        builder.add_import("from concurrent.futures import ThreadPoolExecutor")

        # Generate pipeline execution code
        code = f'''
def pipeline_{dto.id}(input_data):
    """Execute pipeline: {dto.name}"""

    results = []
    with ThreadPoolExecutor(max_workers={dto.parallelism}) as executor:
        futures = []

        for step in {dto.steps!r}:
            step_func = globals()[f"step_{{step['id']}}"]
            future = executor.submit(step_func, input_data)
            futures.append(future)

        results = [f.result() for f in futures]

    return results
'''

        builder.add_lines(code)
        return builder


# ============== Usage ==============

def generate_code_for_entities(entities: list[tuple[CodeEngine, Any]]) -> str:
    """
    Generate code for multiple entities.

    Args:
        entities: List of (engine, dto) tuples

    Returns:
        Generated code as string
    """
    builder = CodeBuilder(lang_spec="python")

    for engine, dto in entities:
        engine.generate(builder, dto)

    return builder.build()


# Example
function_dto = GlobalFunctionDTO(
    id=1,
    name="calculate_discount",
    definition="return price * (1 - discount_rate)",
    input_params=[
        {"name": "price", "type": "float", "is_required": True},
        {"name": "discount_rate", "type": "float", "is_required": False, "default_value": 0.1}
    ],
    output_type="float"
)

prompt_dto = PromptDTO(
    id=2,
    name="summarize",
    template="Summarize: {{ text }}",
    model="gpt-4",
    temperature=0.7
)

code = generate_code_for_entities([
    (GlobalFunctionEngine(), function_dto),
    (PromptEngine(), prompt_dto)
])

print(code)
```

**Why This Matters**:
- ✅ **Open/Closed**: Add new entity types without modifying existing code
- ✅ **Uniform interface**: All engines have `generate(builder, dto)` method
- ✅ **Testability**: Test each engine in isolation
- ✅ **Composability**: Combine multiple entities into one code artifact

**Key Learnings**:
1. ✅ Use **Protocol** for interface definition (no inheritance needed)
2. ✅ Return builder for **method chaining**
3. ✅ Separate **data** (DTOs) from **behavior** (engines)
4. ✅ Inject **metadata** for better error messages

---

## 6. Factory Pattern with Polymorphism

### 6.1 Language Spec Factory

**Problem**: Same logical code needs to run on different backends (Python, PySpark, SQL).

**Solution**: Abstract factory with language-specific implementations.

```python
from abc import ABC, abstractmethod
from enum import Enum

class DataType(Enum):
    INTEGER = "int"
    FLOAT = "float"
    STRING = "str"
    BOOLEAN = "bool"


class LanguageSpec(ABC):
    """Abstract base for language-specific code generation"""

    name: str = None  # Set by subclasses

    @classmethod
    def factory(cls, name: str) -> "LanguageSpec":
        """Factory method to get language spec by name"""
        # Find subclass with matching name
        for subclass in cls.__subclasses__():
            if subclass.name == name:
                return subclass()

        raise ValueError(f"Unknown language spec: {name}")

    @abstractmethod
    def make_literal(self, value: any, data_type: DataType) -> str:
        """Convert Python value to target language literal"""
        pass

    @abstractmethod
    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None
    ) -> str:
        """Generate function definition"""
        pass

    @abstractmethod
    def create_if_statement(self, condition: str, then_block: str, else_block: str = "") -> str:
        """Generate if statement"""
        pass


# ============== Python Implementation ==============

class PythonSpec(LanguageSpec):
    name = "python"

    def make_literal(self, value: any, data_type: DataType) -> str:
        if data_type == DataType.STRING:
            return repr(value)
        return str(value)

    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None
    ) -> str:
        input_str = ", ".join(inputs)
        return_hint = f" -> {output_type}" if output_type else ""

        return f"""def {name}({input_str}){return_hint}:
{self._indent(body, 4)}"""

    def create_if_statement(self, condition: str, then_block: str, else_block: str = "") -> str:
        result = f"if {condition}:\n{self._indent(then_block, 4)}"
        if else_block:
            result += f"\nelse:\n{self._indent(else_block, 4)}"
        return result

    def _indent(self, text: str, spaces: int) -> str:
        return "\n".join(" " * spaces + line for line in text.splitlines())


# ============== SQL Implementation ==============

class SQLSpec(LanguageSpec):
    name = "sql"

    def make_literal(self, value: any, data_type: DataType) -> str:
        if data_type == DataType.STRING:
            return f"'{value}'"
        elif data_type == DataType.BOOLEAN:
            return "TRUE" if value else "FALSE"
        return str(value)

    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None
    ) -> str:
        input_str = ", ".join(inputs)
        return_type = output_type or "TABLE"

        return f"""CREATE OR REPLACE FUNCTION {name}({input_str})
RETURNS {return_type}
AS $$
{body}
$$ LANGUAGE plpgsql;"""

    def create_if_statement(self, condition: str, then_block: str, else_block: str = "") -> str:
        result = f"IF {condition} THEN\n{self._indent(then_block, 2)}"
        if else_block:
            result += f"\nELSE\n{self._indent(else_block, 2)}"
        result += "\nEND IF;"
        return result

    def _indent(self, text: str, spaces: int) -> str:
        return "\n".join(" " * spaces + line for line in text.splitlines())


# ============== PySpark Implementation ==============

class PySparkSpec(LanguageSpec):
    name = "pyspark"

    def make_literal(self, value: any, data_type: DataType) -> str:
        # PySpark uses F.lit()
        python_val = repr(value) if data_type == DataType.STRING else str(value)
        return f"F.lit({python_val})"

    def define_function(
        self,
        name: str,
        body: str,
        inputs: list[str],
        output_type: str | None
    ) -> str:
        # PySpark functions are Python functions that return DataFrame
        input_str = ", ".join(inputs)
        return f"""def {name}({input_str}):
    from pyspark.sql import functions as F
{self._indent(body, 4)}"""

    def create_if_statement(self, condition: str, then_block: str, else_block: str = "") -> str:
        # PySpark uses F.when()
        result = f"F.when({condition}, {then_block})"
        if else_block:
            result += f".otherwise({else_block})"
        return result

    def _indent(self, text: str, spaces: int) -> str:
        return "\n".join(" " * spaces + line for line in text.splitlines())


# ============== Usage ==============

def generate_cross_platform_code(lang: str):
    """Generate same logic in different languages"""
    spec = LanguageSpec.factory(lang)

    # Same logic, different syntax
    discount_func = spec.define_function(
        name="calculate_discount",
        body=spec.create_if_statement(
            condition="price > 100",
            then_block=f"return price * {spec.make_literal(0.9, DataType.FLOAT)}",
            else_block=f"return price * {spec.make_literal(0.95, DataType.FLOAT)}"
        ),
        inputs=["price"],
        output_type="float"
    )

    return discount_func


# Generate Python version
python_code = generate_cross_platform_code("python")
print("Python:")
print(python_code)
print()

# Generate SQL version
sql_code = generate_cross_platform_code("sql")
print("SQL:")
print(sql_code)
print()

# Generate PySpark version
pyspark_code = generate_cross_platform_code("pyspark")
print("PySpark:")
print(pyspark_code)
```

Output:
```python
Python:
def calculate_discount(price) -> float:
    if price > 100:
        return price * 0.9
    else:
        return price * 0.95

SQL:
CREATE OR REPLACE FUNCTION calculate_discount(price)
RETURNS float
AS $$
IF price > 100 THEN
  return price * 0.9
ELSE
  return price * 0.95
END IF;
$$ LANGUAGE plpgsql;

PySpark:
def calculate_discount(price):
    from pyspark.sql import functions as F
    F.when(price > 100, price * F.lit(0.9)).otherwise(price * F.lit(0.95))
```

**Key Learnings**:
1. ✅ Use **factory method** on base class to hide implementations
2. ✅ Store `name` as class attribute for lookup
3. ✅ Abstract common operations (literals, conditionals, functions)
4. ✅ Each spec handles its own syntax quirks

---

## 7. DTO Pattern for Data Transfer

### 7.1 Immutable Dataclasses with Factory Methods

**Problem**: Need to pass data between layers (API → Service → Task) safely.

**Solution**: Immutable DTOs with factory methods for conversion.

```python
import dataclasses
import json
from datetime import datetime
from typing import Any

# ============== Base DTO ==============

@dataclasses.dataclass(frozen=True)
class BaseDTO:
    """Base class for all DTOs with helpful methods"""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return dataclasses.asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON"""
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseDTO":
        """Create from dictionary"""
        field_names = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in field_names}
        return cls(**filtered)


# ============== Domain DTOs ==============

@dataclasses.dataclass(frozen=True, repr=False)
class UserDTO(BaseDTO):
    """Immutable user data transfer object"""

    id: int
    email: str
    username: str
    is_active: bool
    created_at: datetime

    # Optional fields
    full_name: str | None = None
    avatar_url: str | None = None

    def __repr__(self) -> str:
        """Custom repr - don't show sensitive data"""
        return f"UserDTO(id={self.id}, username={self.username!r})"

    @classmethod
    def from_orm(cls, user_model: "User") -> "UserDTO":
        """Factory: ORM model → DTO"""
        return cls(
            id=user_model.id,
            email=user_model.email,
            username=user_model.username,
            is_active=user_model.is_active,
            created_at=user_model.created_at,
            full_name=user_model.full_name,
            avatar_url=user_model.avatar_url
        )

    @classmethod
    def from_api_request(cls, data: dict) -> "UserDTO":
        """Factory: API request → DTO with validation"""
        required = {"email", "username"}
        if not required.issubset(data.keys()):
            raise ValueError(f"Missing required fields: {required - data.keys()}")

        return cls(
            id=data.get("id", 0),  # 0 for new users
            email=data["email"].lower(),
            username=data["username"],
            is_active=data.get("is_active", True),
            created_at=data.get("created_at", datetime.utcnow()),
            full_name=data.get("full_name"),
            avatar_url=data.get("avatar_url")
        )


@dataclasses.dataclass(frozen=True)
class AgentExecutionDTO(BaseDTO):
    """DTO for agent execution tasks"""

    agent_id: int
    session_id: str
    user_message: str
    tools_enabled: list[str]
    max_iterations: int = 10
    temperature: float = 0.7

    # Metadata
    metadata: dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def from_session(cls, session: "ChatSession") -> "AgentExecutionDTO":
        """Factory: Session → DTO"""
        return cls(
            agent_id=session.agent_id,
            session_id=session.id,
            user_message=session.latest_message,
            tools_enabled=session.get_enabled_tools(),
            max_iterations=session.config.get("max_iterations", 10),
            temperature=session.config.get("temperature", 0.7),
            metadata={
                "user_id": session.user_id,
                "created_at": session.created_at.isoformat()
            }
        )


# ============== Usage in Layered Architecture ==============

# Layer 1: API (FastAPI)
from fastapi import APIRouter, Depends

router = APIRouter()

@router.post("/users")
def create_user(data: dict) -> dict:
    """API endpoint - converts request to DTO"""
    # Convert API request → DTO
    user_dto = UserDTO.from_api_request(data)

    # Pass to service layer
    created_user = UserService.create_user(user_dto)

    # Return as dict
    return created_user.to_dict()


# Layer 2: Service
class UserService:
    @staticmethod
    def create_user(dto: UserDTO) -> UserDTO:
        """Service layer - works with DTOs"""
        # Convert DTO → ORM model
        user_model = User(
            email=dto.email,
            username=dto.username,
            is_active=dto.is_active,
            full_name=dto.full_name
        )

        # Save to database
        db.session.add(user_model)
        db.session.commit()

        # Convert ORM → DTO for return
        return UserDTO.from_orm(user_model)


# Layer 3: Background Task (Celery)
from celery import shared_task

@shared_task
def send_welcome_email(user_dto_json: str) -> None:
    """Background task - receives serialized DTO"""
    # Deserialize
    data = json.loads(user_dto_json)
    user_dto = UserDTO.from_dict(data)

    # Use immutable DTO safely
    send_email(
        to=user_dto.email,
        subject=f"Welcome {user_dto.username}!",
        body=f"Hello {user_dto.full_name or user_dto.username}"
    )


# Calling background task
user_dto = UserService.create_user(...)
send_welcome_email.delay(user_dto.to_json())
```

**Advantages of DTOs**:
1. ✅ **Immutability**: `frozen=True` prevents accidental mutations
2. ✅ **Type Safety**: Dataclass provides `__init__` with type hints
3. ✅ **Decoupling**: DTOs don't depend on ORM/framework internals
4. ✅ **Serializable**: Easy to pickle for Celery, convert to JSON
5. ✅ **Explicit**: Clear what data crosses boundaries

**Key Learnings**:
1. ✅ Use `frozen=True` for **value objects**
2. ✅ Add factory methods (`from_orm`, `from_api_request`) for conversions
3. ✅ Implement `__repr__` to hide sensitive data
4. ✅ Use DTOs for **crossing boundaries** (API ↔ Service ↔ Task)
5. ✅ Provide `to_dict()` and `from_dict()` for serialization

---

## 8. Hook System for Extensibility

*(Already covered in Section 1.3 - refer back)*

---

## 9. Advanced ORM Patterns

### 9.1 Smart `get_item()` with Multiple Strategies

**Problem**: Need flexible lookup by ID, unique name, or instance.

**Solution**: Polymorphic `get_item()` with performance optimizations.

```python
from sqlalchemy import select, UniqueConstraint
from sqlalchemy.orm.exc import NoResultFound

class BaseModel:
    """Base model with smart lookup"""

    @classmethod
    def get_item(cls, entity: int | str | "BaseModel") -> "BaseModel":
        """
        Get item by ID, unique name, or passthrough if already instance.

        Args:
            entity: Can be:
                - int: Primary key lookup
                - str: Unique name lookup (if unique constraint exists)
                - BaseModel: Passthrough (no-op)

        Returns:
            Model instance

        Raises:
            NoResultFound: If not found
            NotImplementedError: If invalid type or no unique constraint

        Example:
            user1 = User.get_item(123)           # By ID
            user2 = User.get_item("john_doe")    # By unique username
            user3 = User.get_item(user1)         # Passthrough
        """
        from myapp import db  # Import here to avoid circular imports

        # Strategy 1: Already an instance → passthrough
        if isinstance(entity, cls):
            return entity

        # Strategy 2: Lookup by ID → use session.get() (fast path)
        elif isinstance(entity, int):
            result = db.session.get(cls, {"id": entity})

        # Strategy 3: Lookup by unique name
        elif isinstance(entity, str) and cls._has_unique_name_constraint():
            # Fast path: Check identity map first (already loaded objects)
            result = next(
                (obj for key, obj in db.session.identity_map.items()
                 if key[0] == cls and getattr(obj, "name", None) == entity),
                None
            )

            # Slow path: Query database
            if result is None:
                result = db.session.execute(
                    select(cls).filter_by(name=entity)
                ).scalar_one_or_none()

        else:
            raise NotImplementedError(
                f"{cls.__name__}.get_item() cannot handle {type(entity).__name__}. "
                f"Expected int, str (with unique name), or {cls.__name__} instance."
            )

        if result is None:
            raise NoResultFound(
                f"{cls.__name__} not found: {entity!r}"
            )

        return result

    @classmethod
    def _has_unique_name_constraint(cls) -> bool:
        """Check if 'name' column has unique constraint"""
        for constraint in cls.__table__.constraints:
            if isinstance(constraint, UniqueConstraint):
                col_names = {col.name for col in constraint.columns}
                if col_names == {"name"}:
                    return True
        return False


# Usage
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base, BaseModel):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("name"),)

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255))


# All three work
user_by_id = User.get_item(42)
user_by_name = User.get_item("john_doe")
user_passthrough = User.get_item(user_by_id)  # Same instance

assert user_by_id is user_passthrough  # True (identity map)
```

**Performance Optimizations**:
1. **Identity Map Check**: Before querying, check if object already loaded
2. **session.get()**: For primary key lookups (single query, cached)
3. **Constraint Introspection**: Only allow name lookup if unique constraint exists

**Key Learnings**:
1. ✅ Check `session.identity_map` before querying
2. ✅ Use `session.get()` for PK lookups (optimized)
3. ✅ Introspect `__table__.constraints` for validation
4. ✅ Raise consistent exceptions (`NoResultFound`)

---

### 9.2 Async Version of `get_item()`

```python
from sqlalchemy.ext.asyncio import AsyncSession

class BaseModel:
    @classmethod
    async def aget_item(
        cls,
        entity: int | str | "BaseModel",
        db_session: AsyncSession
    ) -> "BaseModel":
        """Async version of get_item()"""

        if isinstance(entity, cls):
            return entity

        elif isinstance(entity, int):
            result = await db_session.get(cls, {"id": entity})

        elif isinstance(entity, str) and cls._has_unique_name_constraint():
            # Check identity map (synchronous operation)
            result = next(
                (obj for key, obj in db_session.identity_map.items()
                 if key[0] == cls and getattr(obj, "name", None) == entity),
                None
            )

            if result is None:
                stmt = select(cls).filter_by(name=entity)
                result = (await db_session.execute(stmt)).scalar_one_or_none()

        else:
            raise NotImplementedError(f"Cannot handle {type(entity).__name__}")

        if result is None:
            raise NoResultFound(f"{cls.__name__} not found: {entity!r}")

        return result


# Usage with async/await
async def get_user_async(user_ref: int | str) -> User:
    async with async_session() as session:
        user = await User.aget_item(user_ref, session)
        return user
```

---

## 10. Error Handling & Traceability

*(Covered in Section 3.2 - refer back for exec() with traceback support)*

### 10.1 Custom Exception Hierarchy

```python
class AppException(Exception):
    """Base exception for application"""

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "context": self.context
        }


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


# Usage
try:
    user = User.get_item(999)
except NoResultFound:
    raise NotFoundError("User not found", user_id=999, requested_by="admin")

try:
    exec(user_code)
except Exception as e:
    raise ExecutionError(
        "Failed to execute user function",
        code=user_code,
        line_number=traceback.extract_tb(e.__traceback__)[-1].lineno,
        original_error=str(e)
    )
```

---

## 11. Service Layer Architecture

### 11.1 Base Service with Common Operations

```python
from typing import Generic, TypeVar, Type
from sqlalchemy import select
from sqlalchemy.orm import Session

ModelType = TypeVar("ModelType", bound=BaseModel)

class BaseService(Generic[ModelType]):
    """Base service with CRUD operations"""

    model: Type[ModelType]

    def __init__(self, db_session: Session):
        self.db = db_session

    def get_by_id(self, id: int) -> ModelType:
        """Get single item by ID"""
        obj = self.db.get(self.model, id)
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} not found", id=id)
        return obj

    def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        filters: dict | None = None
    ) -> list[ModelType]:
        """List items with pagination"""
        stmt = select(self.model)

        if filters:
            for key, value in filters.items():
                stmt = stmt.filter(getattr(self.model, key) == value)

        stmt = stmt.offset(offset).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def create(self, dto: BaseDTO) -> ModelType:
        """Create new item"""
        obj = self.model(**dto.to_dict())
        self.db.add(obj)
        self.db.flush()  # Get ID without committing
        return obj

    def update(self, id: int, dto: BaseDTO) -> ModelType:
        """Update existing item"""
        obj = self.get_by_id(id)

        for key, value in dto.to_dict().items():
            if hasattr(obj, key):
                setattr(obj, key, value)

        self.db.flush()
        return obj

    def delete(self, id: int) -> None:
        """Delete item"""
        obj = self.get_by_id(id)
        self.db.delete(obj)
        self.db.flush()


# Concrete service
class UserService(BaseService[User]):
    model = User

    def get_by_email(self, email: str) -> User:
        """Custom method for this service"""
        stmt = select(User).filter_by(email=email)
        user = self.db.execute(stmt).scalar_one_or_none()

        if user is None:
            raise NotFoundError("User not found", email=email)

        return user


# Usage
with db.session() as session:
    user_service = UserService(session)

    # Use base methods
    users = user_service.list(filters={"is_active": True}, limit=50)
    user = user_service.get_by_id(123)

    # Use custom methods
    user_by_email = user_service.get_by_email("test@example.com")

    session.commit()
```

---

## 12. Testing Patterns

### 12.1 Test Fixtures with Factories

```python
import pytest
from factory import Factory, Sequence, SubFactory
from factory.alchemy import SQLAlchemyModelFactory

class UserFactory(SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session = db.session

    id = Sequence(lambda n: n)
    email = Sequence(lambda n: f"user{n}@example.com")
    username = Sequence(lambda n: f"user{n}")
    is_active = True


class AgentFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Agent
        sqlalchemy_session = db.session

    id = Sequence(lambda n: n)
    name = Sequence(lambda n: f"Agent {n}")
    created_by = SubFactory(UserFactory)


# Usage in tests
@pytest.fixture
def user():
    return UserFactory()

@pytest.fixture
def agent(user):
    return AgentFactory(created_by=user)

def test_agent_creation(agent):
    assert agent.id is not None
    assert agent.created_by.is_active
```

---

## Summary Checklist

When building a new Python application, use these patterns:

### Architecture
- [ ] Use **DTOs** for data transfer between layers
- [ ] Implement **Service Layer** for business logic
- [ ] Use **Engines/Strategies** for pluggable algorithms
- [ ] Apply **Factory Pattern** for object creation

### Database
- [ ] Create **Mixins** for reusable model behaviors
- [ ] Implement smart `get_item()` with identity map checks
- [ ] Use `@declared_attr` for dynamic attributes
- [ ] Add **hook system** for lifecycle events

### Type System
- [ ] Use `TYPE_CHECKING` for circular imports
- [ ] Apply covariant/contravariant TypeVars correctly
- [ ] Add runtime type inspection where needed
- [ ] Use Protocols for interfaces

### Code Quality
- [ ] Custom `classproperty` for class-level properties
- [ ] Register dynamic code with `linecache`
- [ ] Implement custom exception hierarchy
- [ ] Add metadata for better error messages

### Testing
- [ ] Use Factory pattern for test data
- [ ] Test mixins independently
- [ ] Mock external dependencies
- [ ] Test DTOs serialization

---

**Remember**: Great code looks simple but handles complexity beneath the surface.
