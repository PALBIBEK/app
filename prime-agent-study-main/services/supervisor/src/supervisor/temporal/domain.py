"""Which exceptions are domain errors: final answers a retry cannot change.

Shared by activities (raised as non-retryable ApplicationErrors) and by the
API's in-process mode, so both report the same error kinds.
"""

from __future__ import annotations

from typing import Any

from supervisor.temporal.contracts import NON_RETRYABLE


def domain_kind(exc: BaseException) -> str | None:
    """The exception's class name when retrying cannot fix it, else None.

    Errors that declare ``retryable`` (catalog, harness, policy, sandbox) decide
    for themselves; the rest are matched against NON_RETRYABLE by class or base.
    """
    retryable = getattr(exc, "retryable", None)
    if isinstance(retryable, bool):
        return None if retryable else type(exc).__name__
    if any(cls.__name__ in NON_RETRYABLE for cls in type(exc).__mro__):
        return type(exc).__name__
    return None


def error_details(exc: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {}
    # Policy errors name it error_code; seam errors (DepthExceeded, BadArgs, ...) code.
    if isinstance(code := getattr(exc, "error_code", None) or getattr(exc, "code", None), str):
        details["code"] = code
    if (tools := getattr(exc, "tool_call_ids", None)) is not None:
        details |= {"state": str(getattr(exc, "state", "")), "tool_call_ids": list(tools)}
    return details
