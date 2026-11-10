"""Typed observations: what automatic capture extracts, and how strictly it is read (G2 of
``docs/memory-gaps.md``).

Grok Build's capture, ported: the model answers with one JSON outcome, either ``noop`` or a
list of typed observations (``xai-grok-shell/src/session/memory/v2_capture.rs``), read
strictly. An unknown field anywhere fails the whole outcome, so an injected
``"command": "rm -rf /"`` is malformed output, never data. Each observation is normalized
to the store's limits instead of failing on them (``parse_model_outcome``), and the store
validates every bound again before it keeps one (``ObservationDraft.validate``, from
``xai-grok-memory/src/v2_capture.rs``). The host, never the model, sets the provenance:
the extraction model, the prompt version, and when.

Ours beyond Grok's: QM's sensitivity label rides on the outcome (``sensitivity``, one label
for what was extracted, ``unknown`` when missing or not one of the four), and a JSON answer
wrapped in a code fence is unwrapped (models without structured output add one).

Shared, like ``extraction_prompt``, by the supervisor's capture (which parses the model's
answer) and the memory service (which validates what it is sent).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from supervisor.memory_service.notebooks.types import Sensitivity

__all__ = [
    "MAX_ALIASES",
    "MAX_BODY_BYTES",
    "MAX_KEYWORDS",
    "MAX_MODEL_BYTES",
    "MAX_OBSERVATIONS",
    "MAX_PROMPT_VERSION_BYTES",
    "MAX_STATEMENT_BYTES",
    "MAX_TERM_BYTES",
    "MAX_TOPIC_BYTES",
    "PROMPT_VERSION",
    "MalformedOutcome",
    "Noop",
    "ObservationDraft",
    "ObservationInvalid",
    "ObservationType",
    "Observations",
    "extraction_schema",
    "parse_model_outcome",
]

# Grok's limits (xai-grok-memory/src/v2_capture.rs), unchanged.
MAX_MODEL_BYTES: Final = 128
MAX_PROMPT_VERSION_BYTES: Final = 64
MAX_TOPIC_BYTES: Final = 128
MAX_STATEMENT_BYTES: Final = 1_024
MAX_BODY_BYTES: Final = 8 * 1_024
MAX_KEYWORDS: Final = 16
MAX_ALIASES: Final = 16
MAX_TERM_BYTES: Final = 64
MAX_OBSERVATIONS: Final = 128
MAX_CREATED_AT: Final = 253_402_300_799
"""The last second of the year 9999, in Unix seconds."""

PROMPT_VERSION: Final = "prime-memory-capture-1"
"""The extraction prompt's version, recorded on every observation it produced (Grok's
``memory-v2-capture-1`` for its own prompt; ours is QM's rules with Grok's types)."""


class ObservationInvalid(ValueError):
    """An observation outside the store's bounds; the store keeps none of a capture with one."""


class MalformedOutcome(ValueError):
    """The model's answer is not a valid outcome (retryable: another call may answer well)."""


class ObservationType(StrEnum):
    """What an observation is about (Grok's four)."""

    USER = "user"  # the user: preferences, identifiers, how they like to work (QM's facts)
    FEEDBACK = "feedback"  # the user's corrections and directives about the work
    PROJECT = "project"  # the work: a build command, an invariant, a decision made
    REFERENCE = "reference"  # where something is: a document, a dashboard, a contact

    @classmethod
    def parse(cls, value: str) -> ObservationType:
        try:
            return cls(value)
        except ValueError:
            raise ObservationInvalid(f"unknown observation type {value!r}") from None


@dataclass(frozen=True, slots=True)
class ObservationDraft:
    """One observation as extraction made it, before the store keeps it."""

    observation_type: ObservationType
    topic_hint: str | None
    statement: str
    keywords: tuple[str, ...]
    aliases: tuple[str, ...]
    extraction_model: str
    prompt_version: str
    created_at: int  # Unix seconds
    body: str | None

    def validate(self) -> None:
        """Every bound, or ``ObservationInvalid`` (Grok's ``ObservationDraft::validate``)."""
        _text("statement", self.statement, 1, MAX_STATEMENT_BYTES)
        _text("extraction model", self.extraction_model, 1, MAX_MODEL_BYTES)
        _text("prompt version", self.prompt_version, 1, MAX_PROMPT_VERSION_BYTES)
        if not 0 <= self.created_at <= MAX_CREATED_AT:
            raise ObservationInvalid("created timestamp is outside the supported range")
        if self.topic_hint is not None:
            _text("topic hint", self.topic_hint, 1, MAX_TOPIC_BYTES)
        if self.body is not None and (
            not self.body
            or _bytes(self.body) > MAX_BODY_BYTES
            or any(_is_control(c) and c not in "\n\r\t" for c in self.body)
        ):
            raise ObservationInvalid(f"observation body must contain 1..={MAX_BODY_BYTES} safe bytes")
        _terms("keywords", self.keywords, MAX_KEYWORDS)
        _terms("aliases", self.aliases, MAX_ALIASES)


@dataclass(frozen=True, slots=True)
class Noop:
    """Nothing worth retaining."""

    sensitivity: Sensitivity = Sensitivity.UNKNOWN


@dataclass(frozen=True, slots=True)
class Observations:
    """What the model kept, normalized, and QM's label for all of it."""

    drafts: tuple[ObservationDraft, ...]
    sensitivity: Sensitivity


_OUTCOME_FIELDS: Final = frozenset({"outcome", "observations", "sensitivity"})
_OBSERVATION_FIELDS: Final = frozenset({"type", "topic_hint", "statement", "keywords", "aliases", "body"})
_FENCE: Final = re.compile(r"\A```(?:json)?[ \t]*\n(?P<body>.*)\n```\Z", re.DOTALL)


def parse_model_outcome(text: str, model: str, created_at: int) -> Noop | Observations:
    """The model's answer as an outcome, normalized to the store's limits; the host sets
    the provenance (``model``, ``PROMPT_VERSION``, ``created_at``). ``MalformedOutcome``
    when it is not one (Grok's ``parse_model_outcome``)."""
    stripped = text.strip()
    if fenced := _FENCE.match(stripped):
        stripped = fenced.group("body").strip()
    try:
        parsed: Any = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise MalformedOutcome(f"malformed extraction output: not JSON ({exc.msg})") from None
    outcome = _object(parsed, _OUTCOME_FIELDS, "outcome")
    kind = outcome.get("outcome")
    observations = outcome.get("observations")
    if kind not in ("noop", "observations"):
        raise MalformedOutcome('malformed extraction output: outcome must be "noop" or "observations"')
    if observations is not None and not isinstance(observations, list):
        raise MalformedOutcome("malformed extraction output: observations must be a list")
    sensitivity = Sensitivity.parse(outcome.get("sensitivity"))
    if kind == "noop":
        if observations:
            raise MalformedOutcome("malformed extraction output: noop cannot contain observations")
        return Noop(sensitivity)
    if observations is None:
        raise MalformedOutcome("malformed extraction output: observations outcome requires observations")
    if not observations:
        raise MalformedOutcome("malformed extraction output: observations cannot be empty")
    return Observations(
        tuple(_draft(raw, model, created_at) for raw in observations[:MAX_OBSERVATIONS]), sensitivity
    )


def extraction_schema() -> dict[str, Any]:
    """The outcome's JSON schema, for a model that takes one (Grok's ``extraction_schema``,
    with ``sensitivity`` added); bare, without the ``{name, strict, schema}`` envelope."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["outcome", "observations", "sensitivity"],
        "properties": {
            "outcome": {"type": "string", "enum": ["noop", "observations"]},
            "observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "topic_hint", "statement", "keywords", "aliases", "body"],
                    "properties": {
                        "type": {"type": "string", "enum": [t.value for t in ObservationType]},
                        "topic_hint": {"type": ["string", "null"]},
                        "statement": {"type": "string", "maxLength": MAX_STATEMENT_BYTES},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                        "body": {"type": ["string", "null"]},
                    },
                },
            },
            "sensitivity": {"type": "string", "enum": [s.value for s in Sensitivity]},
        },
    }


# --- reading one observation ----------------------------------------------------------------


def _object(value: Any, fields: frozenset[str], what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedOutcome(f"malformed extraction output: {what} must be an object")
    for name in value:
        if name not in fields:
            raise MalformedOutcome(f"malformed extraction output: unknown field `{name}`")
    return value


def _draft(raw: Any, model: str, created_at: int) -> ObservationDraft:
    observation = _object(raw, _OBSERVATION_FIELDS, "an observation")
    statement = scrub_control_characters(_string(observation, "statement", required=True) or "")
    if _bytes(statement) > MAX_STATEMENT_BYTES:
        raise MalformedOutcome("malformed extraction output: statement exceeds byte limit")
    try:
        kind = ObservationType.parse(_string(observation, "type", required=True) or "")
    except ObservationInvalid as exc:
        raise MalformedOutcome(str(exc)) from None
    topic = _string(observation, "topic_hint")
    body = _string(observation, "body")
    return ObservationDraft(
        observation_type=kind,
        topic_hint=_normalize_term(topic, MAX_TOPIC_BYTES) if topic is not None else None,
        statement=statement,
        keywords=_normalize_terms(_strings(observation, "keywords"), MAX_KEYWORDS),
        aliases=_normalize_terms(_strings(observation, "aliases"), MAX_ALIASES),
        extraction_model=model,
        prompt_version=PROMPT_VERSION,
        created_at=created_at,
        body=_normalize_body(body) if body is not None else None,
    )


def _string(observation: dict[str, Any], name: str, *, required: bool = False) -> str | None:
    value = observation.get(name)
    if value is None:
        if required:
            raise MalformedOutcome(f"malformed extraction output: missing field `{name}`")
        return None
    if not isinstance(value, str):
        raise MalformedOutcome(f"malformed extraction output: `{name}` must be a string")
    return value


def _strings(observation: dict[str, Any], name: str) -> list[str]:
    value = observation.get(name)
    if value is None:
        return []  # serde's #[serde(default)]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise MalformedOutcome(f"malformed extraction output: `{name}` must be a list of strings")
    return value


# --- Grok's normalization ---------------------------------------------------------------------


def _normalize_terms(terms: list[str], max_terms: int) -> tuple[str, ...]:
    kept = (t for t in (_normalize_term(term, MAX_TERM_BYTES) for term in terms) if t is not None)
    return tuple(t for _, t in zip(range(max_terms), kept, strict=False))


def _normalize_term(term: str, max_bytes: int) -> str | None:
    cut = truncate_utf8(scrub_control_characters(term), max_bytes).rstrip()
    return cut or None


def _normalize_body(body: str) -> str | None:
    spaced = "".join(" " if _is_control(c) and c not in "\n\r\t" else c for c in body)
    cut = truncate_utf8(spaced.strip(), MAX_BODY_BYTES).rstrip()
    return cut or None


def scrub_control_characters(text: str) -> str:
    """Control characters as single spaces between the text around them, trimmed."""
    return " ".join(s for s in re.split(_CONTROLS, text) if s).strip()


_CONTROLS: Final = re.compile("[" + "".join(re.escape(chr(c)) for c in [*range(0x20), *range(0x7F, 0xA0)]) + "]")
"""Unicode's control characters (Cc), Rust's ``char::is_control``."""


def truncate_utf8(text: str, max_bytes: int) -> str:
    """At most ``max_bytes`` of UTF-8, cut on a character boundary."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


# --- the store's bounds -------------------------------------------------------------------------


def _text(name: str, value: str, least: int, most: int) -> None:
    if not least <= _bytes(value) <= most:
        raise ObservationInvalid(f"{name} must contain {least}..={most} bytes")
    if any(_is_control(c) for c in value):
        raise ObservationInvalid(f"{name} contains control characters")


def _terms(name: str, values: tuple[str, ...], most: int) -> None:
    if len(values) > most:
        raise ObservationInvalid(f"{name} exceeds the {most}-entry limit")
    for value in values:
        _text(name, value, 1, MAX_TERM_BYTES)


def _is_control(character: str) -> bool:
    return unicodedata.category(character) == "Cc"


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))
