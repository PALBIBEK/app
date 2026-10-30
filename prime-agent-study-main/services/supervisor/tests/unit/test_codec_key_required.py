"""Production refuses to hand Temporal its histories in plain text.

``VAULT_KEYS`` was required in production, ``TEMPORAL_CODEC_KEY`` was not: without it, every
session's messages, answers and tool results were stored by the Temporal server as they are.
"""

from __future__ import annotations

import dataclasses

import pytest

from supervisor.config import TemporalSettings
from supervisor.temporal.client import CodecKeyMissing, require_codec_key

KEY = "a" * 64


def _settings(**over: object) -> TemporalSettings:
    return dataclasses.replace(TemporalSettings(), **{"codec_key": None, "codec_old_keys": (), **over})


@pytest.mark.parametrize("env", ["production", "prod", "staging"])
def test_production_without_a_key_refuses_to_start(env: str) -> None:
    with pytest.raises(CodecKeyMissing, match="TEMPORAL_CODEC_KEY"):
        require_codec_key(_settings(), {"ENV": env})


@pytest.mark.parametrize(
    ("settings", "env"),
    [
        (_settings(codec_key=KEY), {"ENV": "production"}),
        (_settings(codec_old_keys=(KEY,)), {"ENV": "production"}),  # turning encryption on
        (_settings(), {"ENV": "production", "TEMPORAL_PLAINTEXT_HISTORY": "1"}),  # meant
        (_settings(), {"ENV": "development"}),
        (_settings(), {}),
    ],
    ids=["key", "old-keys-only", "plaintext-on-purpose", "development", "unset"],
)
def test_a_key_an_explicit_choice_or_development_may_start(settings: TemporalSettings, env: dict[str, str]) -> None:
    require_codec_key(settings, env)
