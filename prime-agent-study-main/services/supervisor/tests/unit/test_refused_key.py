"""Which gateway refusals a fresh session key can fix (the model step replaces it)."""

from __future__ import annotations

import pytest

from supervisor.agent.runtimes.githi.classify import is_refused_key, is_retryable
from supervisor.seams.core.types import ModelGatewayError


@pytest.mark.parametrize(
    ("status", "code", "refused"),
    [
        (401, "AUTH_INVALID", True),  # unknown key: another signing secret
        (403, "VIRTUAL_KEY_EXPIRED", True),
        (403, "VIRTUAL_KEY_REVOKED", False),  # the session ended
        (429, "VIRTUAL_KEY_BUDGET_EXCEEDED", False),
        (401, "AUTH_REQUIRED", False),  # no key sent at all
    ],
)
def test_which_refusals_a_fresh_key_can_fix(status: int, code: str, refused: bool) -> None:
    error = ModelGatewayError(status, f'{{"detail":{{"error":{{"code":"{code}"}}}}}}')
    assert is_refused_key(error) is refused
    if refused:  # asked again only once the key is replaced, never as is
        assert not is_retryable(error)
