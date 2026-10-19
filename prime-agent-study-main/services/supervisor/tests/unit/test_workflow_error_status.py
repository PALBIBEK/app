"""A policy refusal reaches the caller as a policy status, whichever subclass a worker raised.

Workers report a domain error by its concrete class name, so the API must map
every quota and budget subclass, not only their bases, or a tenant over its
limit sees "503 Service Unavailable" and retries.
"""

from __future__ import annotations

import pytest

from supervisor.api.workflow_errors import to_http
from supervisor.policy.budget import BudgetExceeded
from supervisor.policy.quotas import QuotaExceeded
from supervisor.temporal.sessions import SessionCommandError


def _family(base: type[Exception]) -> list[type[Exception]]:
    found, stack = [base], list(base.__subclasses__())
    while stack:
        cls = stack.pop()
        found.append(cls)
        stack.extend(cls.__subclasses__())
    return found


@pytest.mark.parametrize(
    ("cls", "reason"),
    [(c, "quota_exceeded") for c in _family(QuotaExceeded)]
    + [(c, "budget_exceeded") for c in _family(BudgetExceeded)],
    ids=lambda v: v.__name__ if isinstance(v, type) else v,
)
def test_every_quota_and_budget_refusal_is_a_429(cls: type[Exception], reason: str) -> None:
    http = to_http(SessionCommandError(cls.__name__, "limit reached"))
    assert http.status_code == 429
    assert http.detail == {"reason": reason, "message": "limit reached"}
