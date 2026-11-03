"""Credentials are masked before anything is journalled.

The journal keeps everything a session did, for as long as the session is kept, and
sends it to every client that follows it: a tool that printed ``env``, read a ``.env``
or a key file, or a model that repeated a token it was shown, put that credential in
the journal, in every backup of it and on every screen. Strings that are recognisably
credentials (a provider's key format, a private key block, a JWT, a bearer header, the
value of an ``*_TOKEN=`` / ``*_SECRET=`` / ``*_PASSWORD=`` / ``*_API_KEY=`` assignment,
the platform's own virtual keys) are replaced by ``[redacted:<kind>]`` wherever they
appear in an event's fields. Patterns are chosen to be specific: masking prose or code
that merely looks random would damage what the session is about.
"""

from __future__ import annotations

import re
from typing import Any, Final

from supervisor.journal.serde import decode_event, encode_event

__all__ = ["mask", "mask_event", "mask_text"]

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.DOTALL),
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}")),
    ("virtual_key", re.compile(r"\bvk_[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}")),
    ("slack_token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
)

# Where the name says what the value is: "Authorization: Bearer <token>", an environment
# assignment (UPPER_CASE name: DB_PASSWORD=..., GITHUB_TOKEN: ...) and a quoted JSON / YAML
# field ("api_key": "..."). A value must hold a digit and a letter: code such as
# `token = request.headers` or `PASSWORD=os.environ` names one without being one, and
# `MAX_TOKENS=100000000` is a number.
_BEARER: Final = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{16,})")
_NAME: Final = r"(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CLIENT_?SECRET|CREDENTIALS?)"
_ASSIGNED: Final = re.compile(
    rf"(\b[A-Z0-9_]*{_NAME}[A-Z0-9_]*\s*[=:]\s*[\"']?)((?=[^\s\"']*\d)(?=[^\s\"']*[A-Za-z])[A-Za-z0-9_\-+/=.~:@]{{8,}})"
)
_QUOTED: Final = re.compile(
    rf"(?i)([\"'][a-z0-9_.-]*{_NAME}[a-z0-9_.-]*[\"']\s*:\s*[\"'])((?=[^\"']*\d)(?=[^\"']*[a-z])[^\s\"']{{12,}})"
)
_REDACTED: Final = "[redacted:{}]"


def mask_text(text: str) -> str:
    """``text`` with every credential in it replaced."""
    if len(text) < 8:
        return text
    for kind, pattern in _PATTERNS:
        text = pattern.sub(_REDACTED.format(kind), text)
    text = _BEARER.sub(_named("bearer"), text)
    text = _ASSIGNED.sub(_named("secret"), text)
    return _QUOTED.sub(_named("secret"), text)


def _named(kind: str) -> Any:
    def replace(match: re.Match[str]) -> str:
        return match.group(1) + _REDACTED.format(kind)

    return replace


def mask(value: Any) -> Any:
    """``value`` (plain JSON) with every credential in its strings replaced; the same
    object when there was none, so a caller can tell nothing changed."""
    if isinstance(value, str):
        masked = mask_text(value)
        return value if masked == value else masked
    if isinstance(value, list):
        items = [mask(v) for v in value]
        return value if all(a is b for a, b in zip(items, value, strict=True)) else items
    if isinstance(value, dict):
        fields = {k: mask(v) for k, v in value.items()}
        return value if all(fields[k] is value[k] for k in value) else fields
    return value


def mask_event[E](event: E) -> E:
    """``event`` with every credential in its fields replaced (itself when there was none)."""
    encoded = encode_event(event)  # type: ignore[arg-type]
    masked = mask(encoded)
    return event if masked is encoded else decode_event(masked)  # type: ignore[return-value]
