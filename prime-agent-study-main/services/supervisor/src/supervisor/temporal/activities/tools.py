"""
Tool execution activity with heartbeats and sandbox isolation.

This activity handles tool execution with:
- Sandbox isolation (process, Docker, E2B)
- Heartbeat signaling for long-running tools
- Timeout management per tool
- Structured result/error handling
- Idempotency via tool_call_id

Tool Categories:
    1. Fast tools (< 10s): File read, list, grep
    2. Medium tools (10s - 60s): Code execution, API calls
    3. Long tools (> 60s): Build, deploy, complex computations

Heartbeat Pattern:
    For long-running tools, a background task periodically calls
    activity.heartbeat() to signal liveness. If the activity worker
    crashes, Temporal detects missing heartbeats and retries.

Sandbox Isolation:
    Tools execute in isolated sandboxes for security:
    - Local process: Minimal isolation, development only
    - Docker: Container isolation, resource limits
    - E2B: Cloud sandboxes, full isolation

Example:
    ```python
    result = await workflow.execute_activity(
        "execute_tool",
        args=[session_id, turn_id, tool_call_id, tool_name, arguments],
        start_to_close_timeout=timedelta(seconds=60),
        heartbeat_timeout=timedelta(seconds=30),
    )
    ```
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

logger = logging.getLogger(__name__)

# Injected at runtime
_sandbox_pool: Any = None
_tool_registry: Any = None


class ToolError(ApplicationError):
    """Base error for tool operations."""

    def __init__(self, message: str, error_type: str, retryable: bool = True):
        super().__init__(
            message,
            type=error_type,
            non_retryable=not retryable,
        )


class ToolNotFoundError(ToolError):
    """Tool not found in registry - not retryable."""

    def __init__(self, tool_name: str):
        super().__init__(
            f"Tool not found: {tool_name}",
            "ToolNotFoundError",
            retryable=False,
        )


class InvalidArgumentsError(ToolError):
    """Invalid tool arguments - not retryable."""

    def __init__(self, message: str):
        super().__init__(message, "InvalidArgumentsError", retryable=False)


class PermissionDeniedError(ToolError):
    """Tool execution denied - not retryable."""

    def __init__(self, message: str):
        super().__init__(message, "PermissionDeniedError", retryable=False)


class SandboxError(ToolError):
    """Sandbox execution failed - may be retryable."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message, "SandboxError", retryable=retryable)


class ToolTimeoutError(ToolError):
    """Tool execution timed out - retryable."""

    def __init__(self, tool_name: str, timeout: float):
        super().__init__(
            f"Tool {tool_name} timed out after {timeout}s",
            "ToolTimeoutError",
            retryable=True,
        )


@dataclass
class ToolResult:
    """Result of tool execution."""

    success: bool
    output: str
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] | None = None


def configure_tool_activity(
    sandbox_pool: Any,
    tool_registry: Any | None = None,
) -> None:
    """
    Configure tool activity with dependencies.

    Args:
        sandbox_pool: SandboxPool for tool execution.
        tool_registry: Optional ToolRegistry for validation.
    """
    global _sandbox_pool, _tool_registry
    _sandbox_pool = sandbox_pool
    _tool_registry = tool_registry


def _get_sandbox_pool() -> Any:
    """Get configured sandbox pool or raise."""
    if _sandbox_pool is None:
        raise RuntimeError(
            "Sandbox pool not configured. "
            "Call configure_tool_activity() first."
        )
    return _sandbox_pool


@activity.defn
async def execute_tool(
    session_id: str,
    turn_id: str,
    tool_call_id: str,
    tool_name: str,
    arguments: dict[str, Any] | str,
) -> dict[str, Any]:
    """
    Execute a tool in sandbox with heartbeat monitoring.

    Args:
        session_id: Session context.
        turn_id: Turn context.
        tool_call_id: Unique tool call identifier.
        tool_name: Tool to execute.
        arguments: Tool arguments (dict or JSON string).

    Returns:
        Tool result with success, output, error, duration.

    Raises:
        ToolNotFoundError: If tool doesn't exist.
        InvalidArgumentsError: If arguments are invalid.
        PermissionDeniedError: If execution denied.
        SandboxError: If sandbox fails.
        ToolTimeoutError: If tool times out.
    """
    pool = _get_sandbox_pool()
    info = activity.info()

    # Parse arguments if string
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as e:
            raise InvalidArgumentsError(f"Invalid JSON arguments: {e}") from e

    logger.info(
        f"Executing tool: session={session_id}, tool={tool_name}, "
        f"tool_call_id={tool_call_id}, attempt={info.attempt}"
    )

    # Validate tool exists
    if _tool_registry:
        tool_spec = _tool_registry.get(tool_name)
        if not tool_spec:
            raise ToolNotFoundError(tool_name)

        # Validate arguments
        validation_error = _validate_arguments(tool_spec, arguments)
        if validation_error:
            raise InvalidArgumentsError(validation_error)

    start_time = time.time()

    # Start heartbeat task
    heartbeat_task = asyncio.create_task(_heartbeat_loop(tool_name))

    try:
        # Acquire sandbox from pool
        async with pool.acquire(session_id) as sandbox:
            # Execute tool
            result = await _execute_in_sandbox(
                sandbox,
                tool_name,
                arguments,
                session_id,
            )

            duration = time.time() - start_time

            logger.info(
                f"Tool completed: tool={tool_name}, success={result.success}, "
                f"duration={duration:.2f}s"
            )

            return {
                "success": result.success,
                "output": result.output,
                "error": result.error,
                "duration_seconds": duration,
                "metadata": result.metadata,
            }

    except asyncio.TimeoutError as e:
        duration = time.time() - start_time
        logger.warning(f"Tool timeout: tool={tool_name}, duration={duration:.2f}s")
        raise ToolTimeoutError(tool_name, duration) from e

    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"Tool failed: tool={tool_name}, error={e}")

        # Map to appropriate error type
        if "permission" in str(e).lower():
            raise PermissionDeniedError(str(e)) from e
        if "sandbox" in str(e).lower() or "container" in str(e).lower():
            raise SandboxError(str(e)) from e

        raise

    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat_loop(tool_name: str) -> None:
    """
    Send periodic heartbeats during tool execution.

    Heartbeats signal to Temporal that the activity is still running.
    If heartbeats stop (worker crash), Temporal retries the activity.
    """
    while True:
        try:
            await asyncio.sleep(10)  # Heartbeat every 10 seconds
            activity.heartbeat(f"Executing {tool_name}")
            logger.debug(f"Heartbeat: tool={tool_name}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Heartbeat error: {e}")
            break


def _validate_arguments(
    tool_spec: dict[str, Any],
    arguments: dict[str, Any],
) -> str | None:
    """
    Validate tool arguments against spec.

    Returns error message if invalid, None if valid.
    """
    parameters = tool_spec.get("parameters", {})
    required = parameters.get("required", [])
    properties = parameters.get("properties", {})

    # Check required arguments
    for req in required:
        if req not in arguments:
            return f"Missing required argument: {req}"

    # Check argument types (basic validation)
    for arg_name, arg_value in arguments.items():
        if arg_name in properties:
            expected_type = properties[arg_name].get("type")
            if expected_type and not _check_type(arg_value, expected_type):
                return f"Invalid type for {arg_name}: expected {expected_type}"

    return None


def _check_type(value: Any, expected_type: str) -> bool:
    """Check if value matches expected JSON schema type."""
    type_map = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    expected = type_map.get(expected_type)
    if expected is None:
        return True  # Unknown type, allow

    return isinstance(value, expected)


async def _execute_in_sandbox(
    sandbox: Any,
    tool_name: str,
    arguments: dict[str, Any],
    session_id: str,
) -> ToolResult:
    """
    Execute tool in sandbox environment.

    Routes to appropriate tool handler based on tool name.
    """
    # Built-in tools
    if tool_name == "read_file":
        return await _tool_read_file(sandbox, arguments)
    elif tool_name == "write_file":
        return await _tool_write_file(sandbox, arguments)
    elif tool_name == "list_directory":
        return await _tool_list_directory(sandbox, arguments)
    elif tool_name == "execute_command":
        return await _tool_execute_command(sandbox, arguments)
    elif tool_name == "grep":
        return await _tool_grep(sandbox, arguments)

    # Custom tools via sandbox exec
    return await _tool_custom(sandbox, tool_name, arguments)


async def _tool_read_file(
    sandbox: Any,
    arguments: dict[str, Any],
) -> ToolResult:
    """Read file contents."""
    path = arguments.get("path", "")
    if not path:
        return ToolResult(success=False, error="path is required")

    try:
        content = await sandbox.read_file(path)
        return ToolResult(success=True, output=content)
    except FileNotFoundError:
        return ToolResult(success=False, error=f"File not found: {path}")
    except PermissionError:
        return ToolResult(success=False, error=f"Permission denied: {path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def _tool_write_file(
    sandbox: Any,
    arguments: dict[str, Any],
) -> ToolResult:
    """Write file contents."""
    path = arguments.get("path", "")
    content = arguments.get("content", "")

    if not path:
        return ToolResult(success=False, error="path is required")

    try:
        await sandbox.write_file(path, content)
        return ToolResult(success=True, output=f"Wrote {len(content)} bytes to {path}")
    except PermissionError:
        return ToolResult(success=False, error=f"Permission denied: {path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def _tool_list_directory(
    sandbox: Any,
    arguments: dict[str, Any],
) -> ToolResult:
    """List directory contents."""
    path = arguments.get("path", ".")

    try:
        entries = await sandbox.list_directory(path)
        output = "\n".join(entries) if entries else "(empty directory)"
        return ToolResult(success=True, output=output, metadata={"count": len(entries)})
    except FileNotFoundError:
        return ToolResult(success=False, error=f"Directory not found: {path}")
    except NotADirectoryError:
        return ToolResult(success=False, error=f"Not a directory: {path}")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def _tool_execute_command(
    sandbox: Any,
    arguments: dict[str, Any],
) -> ToolResult:
    """Execute shell command."""
    command = arguments.get("command", "")
    timeout = arguments.get("timeout", 30)

    if not command:
        return ToolResult(success=False, error="command is required")

    try:
        result = await asyncio.wait_for(
            sandbox.execute(command),
            timeout=timeout,
        )

        return ToolResult(
            success=result.exit_code == 0,
            output=result.stdout,
            error=result.stderr if result.exit_code != 0 else None,
            metadata={
                "exit_code": result.exit_code,
                "stdout_len": len(result.stdout),
                "stderr_len": len(result.stderr),
            },
        )
    except asyncio.TimeoutError:
        return ToolResult(success=False, error=f"Command timed out after {timeout}s")
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def _tool_grep(
    sandbox: Any,
    arguments: dict[str, Any],
) -> ToolResult:
    """Search file contents."""
    pattern = arguments.get("pattern", "")
    path = arguments.get("path", ".")
    recursive = arguments.get("recursive", True)

    if not pattern:
        return ToolResult(success=False, error="pattern is required")

    try:
        matches = await sandbox.grep(pattern, path, recursive=recursive)
        output = "\n".join(
            f"{m.file}:{m.line}: {m.content}" for m in matches[:100]
        )

        return ToolResult(
            success=True,
            output=output or "(no matches)",
            metadata={"match_count": len(matches)},
        )
    except Exception as e:
        return ToolResult(success=False, error=str(e))


async def _tool_custom(
    sandbox: Any,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    """Execute custom tool via sandbox."""
    try:
        result = await sandbox.exec_tool(tool_name, arguments)

        if isinstance(result, dict):
            return ToolResult(
                success=result.get("success", True),
                output=result.get("output", str(result)),
                error=result.get("error"),
                metadata=result.get("metadata"),
            )

        return ToolResult(success=True, output=str(result))

    except Exception as e:
        return ToolResult(success=False, error=str(e))
