"""A hand's key: which session it serves, and which of that session's hands it is.

A session has a default hand (its composition's ``sandbox_profile``) and may pin
more by name (``hands``). Everything that holds a sandbox (the hands service,
checkpoints, sandbox labels, adoption, the orphan reaper) keys it by this one
string, so a named hand is built, checkpointed, adopted and reaped exactly like
the default one:

* the default hand's key is the session id itself;
* a named hand's key is ``{session_id}~{name}``.

Session ids never contain ``~`` (they are hex), so a key always splits back.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = ["DEFAULT_HAND", "HAND_ARG", "hand_key", "split_key", "take_hand", "with_hand_argument"]

DEFAULT_HAND: Final = "default"
_SEPARATOR: Final = "~"


def hand_key(session_id: str, hand: str | None = None) -> str:
    return session_id if hand in (None, "", DEFAULT_HAND) else f"{session_id}{_SEPARATOR}{hand}"


def split_key(key: str) -> tuple[str, str]:
    """(session id, hand name) of a key; a plain session id is its default hand."""
    session_id, sep, hand = key.partition(_SEPARATOR)
    return (session_id, hand) if sep else (key, DEFAULT_HAND)


HAND_ARG: Final = "hand"
"""The argument every sandbox tool takes when the session has named hands."""


def take_hand(arguments: dict[str, Any], hands: list[str]) -> tuple[dict[str, Any], str | None]:
    """The call's arguments without ``hand``, and the hand it names (None: the default).

    Only a session with named hands offers the argument; for any other the
    arguments are left exactly as the model sent them.
    """
    if len(hands) <= 1 or HAND_ARG not in arguments:
        return arguments, None
    rest = {k: v for k, v in arguments.items() if k != HAND_ARG}
    named = arguments[HAND_ARG]
    return rest, str(named) if named not in (None, "") else None


def with_hand_argument(schema: dict[str, Any], hands: list[str]) -> dict[str, Any]:
    """A sandbox tool's schema offering the session's hands (unchanged with only the default)."""
    if len(hands) <= 1:
        return schema
    function = dict(schema.get("function") or {})
    parameters = dict(function.get("parameters") or {"type": "object"})
    properties = dict(parameters.get("properties") or {})
    properties[HAND_ARG] = {
        "type": "string",
        "enum": list(hands),
        "description": f"Which of your sandboxes to run in (default: {DEFAULT_HAND}).",
    }
    parameters["properties"] = properties
    function["parameters"] = parameters
    return {**schema, "function": function}
