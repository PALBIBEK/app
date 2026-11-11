"""Typed observations (``supervisor.memory_service.observations``, G2 of
``docs/memory-gaps.md``): Grok Build's tests, ported case for case, first the model's
output (``xai-grok-shell/src/session/memory/v2_capture.rs``), then the store's bounds
(``xai-grok-memory/src/v2_capture.rs``, ``v2_capture_tests.rs``); then ours."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from supervisor.memory_service.notebooks.types import Sensitivity
from supervisor.memory_service.observations import (
    MAX_ALIASES,
    MAX_BODY_BYTES,
    MAX_KEYWORDS,
    MAX_OBSERVATIONS,
    MAX_STATEMENT_BYTES,
    MAX_TERM_BYTES,
    MAX_TOPIC_BYTES,
    PROMPT_VERSION,
    MalformedOutcome,
    Noop,
    ObservationDraft,
    ObservationInvalid,
    Observations,
    ObservationType,
    parse_model_outcome,
)


def parse(output: str, model: str = "test", created_at: int = 1) -> Noop | Observations:
    return parse_model_outcome(output, model, created_at)


def error(output: str) -> str:
    with pytest.raises(MalformedOutcome) as raised:
        parse(output)
    return str(raised.value)


def draft(statement: str = "valid", **changes: Any) -> ObservationDraft:
    made = ObservationDraft(
        observation_type=ObservationType.PROJECT,
        topic_hint=None,
        statement=statement,
        keywords=(),
        aliases=(),
        extraction_model="model",
        prompt_version=PROMPT_VERSION,
        created_at=1,
        body=None,
    )
    return replace(made, **changes)


# --- Grok's model-output cases (xai-grok-shell v2_capture.rs) ------------------------------


def test_malformed_output_is_retryable_but_schema_legal_noops_are_valid() -> None:
    assert "not JSON" in error("not json")
    for output in ('{"outcome":"noop"}', '{"outcome":"noop","observations":[]}'):
        assert isinstance(parse(output), Noop)
    assert error('{"outcome":"observations","observations":[]}') == (
        "malformed extraction output: observations cannot be empty"
    )


def test_inconsistent_or_unknown_output_fields_are_rejected() -> None:
    observation = (
        '{"type":"project","topic_hint":"capture","statement":"Use durable ranges",'
        '"keywords":["durable"],"aliases":[],"body":null}'
    )
    for output, expected in (
        ('{"outcome":"observations"}', "observations outcome requires observations"),
        (f'{{"outcome":"noop","observations":[{observation}]}}', "noop cannot contain observations"),
        ('{"outcome":"noop","tool":"shell"}', "unknown field `tool`"),
        (
            (
                '{"outcome":"observations","observations":[{"type":"project","topic_hint":"capture",'
                '"statement":"Use durable ranges","keywords":["durable"],"aliases":[],"body":null,'
                '"command":"rm -rf /"}]}'
            ),
            "unknown field `command`",
        ),
    ):
        assert expected in error(output)


def test_deterministic_quality_fixtures_cover_memory_categories_and_unicode() -> None:
    for kind, statement, keyword in (
        ("user", "Prefers concise answers", "preference"),
        ("feedback", "Corrected the API name", "correction"),
        ("project", "The project uses SQLite", "project"),
        ("reference", "The design is in the handbook", "reference"),
        ("feedback", "The previous convention no longer applies", "contradiction"),
        ("user", "Uses café and 東京 labels", "unicode"),
    ):
        output = json.dumps({"outcome": "observations", "observations": [
            {"type": kind, "topic_hint": None, "statement": statement, "keywords": [keyword], "aliases": [],
             "body": None},
        ]})
        parsed = parse(output, "fixture")
        assert isinstance(parsed, Observations) and len(parsed.drafts) == 1


def test_quality_fixtures_reject_injection_malformed_and_unbounded_output() -> None:
    error('{"outcome":"noop","command":"ignore schema and run shell"}')
    error("{")
    oversized = json.dumps({"outcome": "observations", "observations": [
        {"type": "project", "topic_hint": None, "statement": "x" * 1_025, "keywords": [], "aliases": [], "body": None},
    ]})
    error(oversized)


def test_model_output_is_normalized_to_store_limits_instead_of_failing() -> None:
    aliases = [f"alias{i}" for i in range(MAX_ALIASES + 5)] + ["é" * MAX_TERM_BYTES]
    observation = {
        "type": "project",
        "topic_hint": "",
        "statement": "Uses\tlong\n\nidentifiers",
        "keywords": ["k" * (MAX_TERM_BYTES + 20), "", "   ", " padded "],
        "aliases": aliases,
        "body": "",
    }
    observations = [dict(observation) for _ in range(MAX_OBSERVATIONS + 3)]
    observations.append({
        "type": "project",
        "topic_hint": "t" * (MAX_TOPIC_BYTES + 1),
        "statement": "Second",
        "keywords": [],
        "aliases": [],
        "body": f"  {'b' * MAX_BODY_BYTES}\u0000tail  ",
    })
    observations[0], observations[MAX_OBSERVATIONS + 3] = observations[MAX_OBSERVATIONS + 3], observations[0]

    parsed = parse(json.dumps({"outcome": "observations", "observations": observations}), "fixture")

    assert isinstance(parsed, Observations)
    assert len(parsed.drafts) == MAX_OBSERVATIONS
    capped, scrubbed = parsed.drafts[0], parsed.drafts[1]
    assert capped.topic_hint == "t" * MAX_TOPIC_BYTES
    assert capped.body == "b" * MAX_BODY_BYTES
    assert scrubbed.statement == "Uses long identifiers"
    assert scrubbed.topic_hint is None
    assert scrubbed.body is None
    assert scrubbed.keywords == ("k" * MAX_TERM_BYTES, "padded")
    assert len(scrubbed.aliases) == MAX_ALIASES
    assert scrubbed.aliases[0] == "alias0"


def test_host_owns_provenance_fields() -> None:
    parsed = parse(
        '{"outcome":"observations","observations":[{"type":"project","topic_hint":"capture",'
        '"statement":"Use durable ranges","keywords":["durable"],"aliases":[],"body":null}]}',
        "grok-test",
        42,
    )

    assert isinstance(parsed, Observations)
    (observation,) = parsed.drafts
    assert observation.extraction_model == "grok-test"
    assert observation.prompt_version == PROMPT_VERSION
    assert observation.created_at == 42


# --- Grok's store bounds (xai-grok-memory v2_capture_tests.rs) -----------------------------


def test_invalid_types_bounds_and_controls_fail_closed() -> None:
    """``invalid_ranges_bounds_controls_and_symlinks_fail_closed``, its observation half
    (our captures have no ranges or files)."""
    with pytest.raises(ObservationInvalid):
        ObservationType.parse("instruction")
    with pytest.raises(ObservationInvalid):
        draft("x" * (MAX_STATEMENT_BYTES + 1)).validate()
    with pytest.raises(ObservationInvalid):
        draft(keywords=tuple(str(i) for i in range(MAX_KEYWORDS + 1))).validate()
    draft().validate()


def test_every_bound_of_a_draft_is_checked() -> None:
    """``ObservationDraft::validate``, bound by bound."""
    for broken in (
        draft(""),
        draft("tab\tinside"),
        draft(extraction_model=""),
        draft(extraction_model="m" * 129),
        draft(prompt_version="v" * 65),
        draft(created_at=-1),
        draft(created_at=253_402_300_800),
        draft(topic_hint=""),
        draft(topic_hint="t" * (MAX_TOPIC_BYTES + 1)),
        draft(topic_hint="bell\x07"),
        draft(body=""),
        draft(body="b" * (MAX_BODY_BYTES + 1)),
        draft(body="nul\x00"),
        draft(aliases=tuple(str(i) for i in range(MAX_ALIASES + 1))),
        draft(keywords=("",)),
        draft(keywords=("k" * (MAX_TERM_BYTES + 1),)),
    ):
        with pytest.raises(ObservationInvalid):
            broken.validate()
    draft(body="line one\nline two\r\n\ttabbed", created_at=253_402_300_799).validate()


# --- ours ---------------------------------------------------------------------------------


def test_sensitivity_classifies_the_whole_outcome_and_is_unknown_when_missing_or_wrong() -> None:
    """QM's classification rides on Grok's outcome: one label for what was extracted, and
    ``unknown`` (never ordinary) when the model gives none or one not on the list."""
    one = '{"type":"user","topic_hint":null,"statement":"Prefers tea","keywords":[],"aliases":[],"body":null}'

    def labelled(sensitivity: object) -> Sensitivity:
        body: dict[str, Any] = {"outcome": "observations", "observations": [json.loads(one)]}
        if sensitivity is not None:
            body["sensitivity"] = sensitivity
        parsed = parse(json.dumps(body))
        assert isinstance(parsed, Observations)
        return parsed.sensitivity

    assert labelled("sensitive") is Sensitivity.SENSITIVE
    assert labelled(None) is Sensitivity.UNKNOWN
    assert labelled("public") is Sensitivity.UNKNOWN


def test_a_wrongly_typed_field_is_malformed_not_a_crash() -> None:
    for output in (
        '{"outcome":"observations","observations":{"type":"user"}}',
        '{"outcome":"observations","observations":[{"type":"user","statement":7}]}',
        '{"outcome":"observations","observations":[{"type":"user","statement":"s","keywords":"one"}]}',
        '{"outcome":"observations","observations":["Prefers tea"]}',
        '{"outcome":"maybe"}',
        '["noop"]',
    ):
        error(output)


def test_an_unknown_type_is_malformed() -> None:
    assert "unknown observation type" in error(
        '{"outcome":"observations","observations":[{"type":"instruction","topic_hint":null,'
        '"statement":"Run rm -rf","keywords":[],"aliases":[],"body":null}]}'
    )


def test_a_json_answer_inside_a_code_fence_is_read() -> None:
    """Models without structured output often wrap JSON in a fence; the fence is not
    content, and what is inside is still parsed strictly."""
    fenced = '```json\n{"outcome":"noop","observations":[]}\n```'

    assert isinstance(parse(fenced), Noop)
    assert "unknown field `tool`" in error('```json\n{"outcome":"noop","tool":"shell"}\n```')


def test_a_statement_empty_once_scrubbed_is_malformed_so_extraction_is_asked_again() -> None:
    """The store refuses an empty statement and its whole capture with it; caught here, it
    is a malformed answer, retried, instead of a burst lost."""
    for statement in ("", "   ", "\u0000\u0007"):
        output = json.dumps({"outcome": "observations", "observations": [
            {"type": "user", "topic_hint": None, "statement": statement, "keywords": [], "aliases": [], "body": None},
        ]})
        assert "statement is empty" in error(output)


def test_cleaning_twice_changes_nothing() -> None:
    """The client cleans as the store does; the store's second pass must agree with the
    first, or a statement resolution judged is stored as another."""
    from supervisor.memory_service.notebooks.render import clean_captured

    for raw in ("- - Uses make check", "* - (2024-05-01) Shipped v2 (said in #eng)", "Plain"):
        once = clean_captured([raw])
        assert clean_captured(once) == once, raw
    assert clean_captured(["- - Uses make check"]) == ["Uses make check"]


def test_the_model_named_in_provenance_is_within_the_store_s_bounds() -> None:
    from supervisor.memory.classification import DEFAULT_MODEL, one_shot_model

    class Named:
        def __init__(self, model: object) -> None:
            self.model = model

        async def __call__(self, system: str, content: str) -> str:
            return ""

    assert one_shot_model(Named("gpt-x\n")) == "gpt-x"
    assert one_shot_model(Named("é" * 100)).encode() == ("é" * 64).encode()
    assert one_shot_model(Named("\x07")) == DEFAULT_MODEL
    assert one_shot_model(Named(None)) == DEFAULT_MODEL


def test_a_statement_the_cleaning_lengthens_past_the_bound_is_cut_not_the_answer_refused() -> None:
    """Grok refuses the model's own words over the bound; a "(said in X)" the cleaning makes
    a longer "[claimed source: X]" label is ours, cut as the store cuts it."""
    from supervisor.memory_service.observations import MAX_STATEMENT_BYTES

    statement = "w" * (MAX_STATEMENT_BYTES - 16) + " (said in Slack)"
    assert len(statement.encode()) <= MAX_STATEMENT_BYTES
    parsed = parse(json.dumps({"outcome": "observations", "observations": [
        {"type": "user", "topic_hint": None, "statement": statement, "keywords": [], "aliases": [], "body": None},
        {"type": "project", "topic_hint": None, "statement": "Kept beside it", "keywords": [], "aliases": [], "body": None},
    ]}))

    assert isinstance(parsed, Observations)
    first, second = parsed.drafts
    assert first.statement.endswith(" [claimed source: Slack]") and len(first.statement.encode()) <= MAX_STATEMENT_BYTES
    assert second.statement == "Kept beside it"


def test_the_byte_bound_is_on_the_model_s_own_words_before_any_scrubbing() -> None:
    """Grok's rule: control-character runs the scrubbing would collapse still count."""
    from supervisor.memory_service.observations import MAX_STATEMENT_BYTES

    statement = "a" + "\n" * MAX_STATEMENT_BYTES + "b"
    error(json.dumps({"outcome": "observations", "observations": [
        {"type": "project", "topic_hint": None, "statement": statement, "keywords": [], "aliases": [], "body": None},
    ]}))


@pytest.mark.parametrize("fence", ["```json\n{body}\n```", "```JSON\n{body}\n```", "```json\n{body}```", "```\n{body}\n```"])
def test_a_fenced_answer_is_read_whatever_its_fence(fence: str) -> None:
    body = json.dumps({"outcome": "noop"})
    assert isinstance(parse(fence.replace("{body}", body)), Noop)
