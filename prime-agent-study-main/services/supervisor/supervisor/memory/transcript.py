"""The durable transcript automatic capture reads: a burst's whole turns, condensed to fit.

Grok Build's ``capture_transcript.rs`` (``xai-grok-shell/src/session/memory``), on our
journal. Capture used to read only each run's first user message and its final reply, so
what a turn learned through its tools (a file's contents, a command's output, a page) never
reached extraction, and a run the user stopped captured nothing. Here the extractor gets
every step of the burst's runs: the user's messages, the assistant's replies with their
tool calls, each tool's result, the model's readable reasoning, and where the user stopped
a run.

Within a budget that shrinks with each attempt (``TranscriptBudget.for_attempt``), stages
remove what the extractor needs least before what it needs most, and condensing never
fails on size:

1. readable reasoning, and images (a text extractor cannot read an image; their
   placeholders stay);
2. the middle of tool results and tool arguments, in three tiers;
3. the middle of the tool loop, keeping its first and last steps;
4. last, the middle of the user's own words, never below a floor.

Encrypted or signed reasoning (an opaque blob only its own model can read) never enters
the transcript: only reasoning's readable text does. A condensed transcript comes with a
note for the extractor saying what was removed (``CondensationStats.prompt_note``).

The work is linear in the burst's events and touches nothing but them, so many sessions'
captures condense side by side without sharing anything.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.agent.events import Envelope, MessageEnd, ToolEnd, TurnEnd

__all__ = [
    "BASE_MAX_TRANSCRIPT_BYTES",
    "BASE_MAX_TRANSCRIPT_ITEMS",
    "STOPPED_MARKER",
    "CondensationStats",
    "CondensedTranscript",
    "Item",
    "TranscriptBudget",
    "condense",
    "run_items",
    "trim_middle",
]

BASE_MAX_TRANSCRIPT_BYTES: Final = 512 * 1024
"""About 130k tokens of JSON on the first attempt; each retry halves it."""
BASE_MAX_TRANSCRIPT_ITEMS: Final = 512
MIN_MAX_TRANSCRIPT_BYTES: Final = 64 * 1024
MIN_MAX_TRANSCRIPT_ITEMS: Final = 64
TOOL_RESULT_TIERS: Final = (4 * 1024, 1024, 256)
TOOL_ARGS_TIERS: Final = (2 * 1024, 512, 256)
KEEP_EDGE_START: Final = 8
USER_TEXT_FLOOR_BYTES: Final = 4 * 1024
STOPPED_MARKER: Final = "[... the user stopped this turn here, before it finished ...]"
"""Where a run the user stopped ends: what it did up to here is unfinished work."""
_IMAGE: Final = "[... image omitted ...]"
_STEPS_OMITTED: Final = "[... intermediate steps omitted ...]"
_IMAGE_PARTS: Final = frozenset({"image", "image_url", "input_image"})

type Item = dict[str, Any]


@dataclass(frozen=True, slots=True)
class TranscriptBudget:
    max_bytes: int
    max_items: int

    @classmethod
    def for_attempt(cls, attempt: int) -> TranscriptBudget:
        """Attempt 1 gets the base budget; each later attempt half the one before, down to
        the floors (an extraction that failed on a long transcript retries on a shorter one)."""
        shift = min(max(attempt - 1, 0), 8)
        return cls(
            max(BASE_MAX_TRANSCRIPT_BYTES >> shift, MIN_MAX_TRANSCRIPT_BYTES),
            max(BASE_MAX_TRANSCRIPT_ITEMS >> shift, MIN_MAX_TRANSCRIPT_ITEMS),
        )

    def fits(self, encoded: str, items: int) -> bool:
        return _size(encoded) <= self.max_bytes and items <= self.max_items


@dataclass(slots=True)
class CondensationStats:
    original_bytes: int = 0
    original_items: int = 0
    final_bytes: int = 0
    final_items: int = 0
    reasoning_dropped: int = 0
    images_dropped: int = 0
    tool_results_trimmed: int = 0
    tool_args_trimmed: int = 0
    steps_omitted: int = 0
    user_text_trimmed: int = 0
    over_budget: bool = False

    def is_condensed(self) -> bool:
        return any(
            (self.reasoning_dropped, self.images_dropped, self.tool_results_trimmed, self.tool_args_trimmed,
             self.steps_omitted, self.user_text_trimmed)
        )

    def prompt_note(self) -> str | None:
        """What the extractor is told was removed; None when nothing was."""
        if not self.is_condensed():
            return None
        parts = [
            text
            for count, text in (
                (self.reasoning_dropped, "model reasoning omitted"),
                (self.images_dropped, "images omitted"),
                (self.tool_results_trimmed, "tool outputs truncated"),
                (self.tool_args_trimmed, "tool arguments truncated"),
                (self.steps_omitted, f"{self.steps_omitted} intermediate steps omitted"),
                (self.user_text_trimmed, "user text truncated"),
            )
            if count
        ]
        return (
            f"The transcript was condensed to fit the extraction budget ({', '.join(parts)}). "
            'Text between "[..." and "...]" markers was removed, not written by a participant.'
        )


@dataclass(frozen=True, slots=True)
class CondensedTranscript:
    json: str
    stats: CondensationStats = field(default_factory=CondensationStats)


# --- from the journal ---------------------------------------------------------------------


def run_items(events: Iterable[Envelope]) -> list[Item]:
    """One run's transcript items, in journal order: ``user``, ``reasoning``, ``assistant``
    (its text and tool calls) and ``tool_result`` items, and the stop marker where the user
    stopped the run. Only readable text is kept: a reasoning part's signature or encrypted
    content, and a redacted block, never are."""
    items: list[Item] = []
    for envelope in events:
        event = envelope.payload
        if isinstance(event, MessageEnd) and event.role == "user":
            items.append({"type": "user", "content": _user_parts(event.content)})
        elif isinstance(event, MessageEnd) and event.role == "assistant":
            items.extend(_assistant_items(event.content))
        elif isinstance(event, ToolEnd):
            items.append(_tool_result(event))
        elif isinstance(event, TurnEnd) and event.stop_reason == "aborted":
            items.append({"type": "assistant", "content": STOPPED_MARKER, "tool_calls": []})
    return items


def _user_parts(content: Sequence[Mapping[str, Any]]) -> list[Item]:
    parts: list[Item] = []
    for part in content:
        if part.get("type") == "text":
            parts.append({"type": "text", "text": str(part.get("text", ""))})
        elif part.get("type") in _IMAGE_PARTS:
            parts.append({"type": "image"})
    return parts


def _assistant_items(content: Sequence[Mapping[str, Any]]) -> list[Item]:
    reasoning = [
        {"type": "reasoning", "text": str(part["text"])}
        for part in content
        if part.get("type") == "reasoning" and part.get("text") and not part.get("redacted")
    ]
    text = "".join(str(part.get("text", "")) for part in content if part.get("type") == "text")
    calls = [
        {
            "id": str(part.get("id", "")),
            "name": str(part.get("name", "")),
            "arguments": json.dumps(part.get("arguments") or {}, ensure_ascii=False, sort_keys=True),
        }
        for part in content
        if part.get("type") == "tool_call"
    ]
    return reasoning + ([{"type": "assistant", "content": text, "tool_calls": calls}] if text or calls else [])


def _tool_result(event: ToolEnd) -> Item:
    result = event.result or {}
    payload = result.get("payload")
    images: list[Item] = []
    if isinstance(payload, Mapping) and isinstance(payload.get("images"), list):
        images = [{"type": "image"} for _ in payload["images"]]
        payload = {k: v for k, v in payload.items() if k != "images"}
    if event.is_error or result.get("ok") is False:
        body = str(result.get("error") or payload or "the call failed")
    else:
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    return {"type": "tool_result", "call_id": event.tool_call_id, "name": event.tool_name, "content": body,
            "is_error": bool(event.is_error or result.get("ok") is False), "images": images}


# --- condensing ---------------------------------------------------------------------------


def condense(items: list[Item], budget: TranscriptBudget) -> CondensedTranscript:
    """``items`` as the extractor reads them, within ``budget`` (or as close as the user's
    own words allow, ``stats.over_budget``)."""
    items = [_copy(item) for item in items]
    stats = CondensationStats(original_items=len(items))
    encoded = _serialize(items)
    stats.original_bytes = _size(encoded)
    # Images first, whatever the budget: the extractor reads text, and an image's bytes are
    # its whole size (Grok strips them at this stage too; its extractor could take images).
    stats.images_dropped = sum(_strip_images(item) for item in items)
    if stats.images_dropped:
        encoded = _serialize(items)
    if budget.fits(encoded, len(items)):
        return _finish(encoded, len(items), stats)

    before = len(items)
    items = [item for item in items if item["type"] != "reasoning"]
    stats.reasoning_dropped = before - len(items)
    encoded = _serialize(items)
    if budget.fits(encoded, len(items)):
        return _finish(encoded, len(items), stats)

    for result_cap, args_cap in zip(TOOL_RESULT_TIERS, TOOL_ARGS_TIERS, strict=True):
        for item in items:
            if item["type"] == "tool_result" and (trimmed := trim_middle(item["content"], result_cap)) is not None:
                item["content"] = trimmed
                stats.tool_results_trimmed += 1
            elif item["type"] == "assistant":
                for call in item["tool_calls"]:
                    if (trimmed := trim_middle(call["arguments"], args_cap)) is not None:
                        call["arguments"] = trimmed
                        stats.tool_args_trimmed += 1
        encoded = _serialize(items)
        if budget.fits(encoded, len(items)):
            return _finish(encoded, len(items), stats)

    keep_edge = KEEP_EDGE_START
    while True:
        reduced, omitted = _omit_middle_steps(items, keep_edge)
        reduced_encoded = _serialize(reduced)
        if budget.fits(reduced_encoded, len(reduced)) or keep_edge == 1:
            items, encoded, stats.steps_omitted = reduced, reduced_encoded, omitted
            if budget.fits(encoded, len(items)):
                return _finish(encoded, len(items), stats)
            break
        keep_edge //= 2

    users = max(1, sum(1 for item in items if item["type"] == "user"))
    others = sum(_size(_serialize(item)) for item in items if item["type"] != "user")
    share = max((budget.max_bytes - others) // users if budget.max_bytes > others else 0, USER_TEXT_FLOOR_BYTES)
    while True:
        trimmed_any = False
        for item in items:
            if item["type"] == "user":
                for part in item["content"]:
                    if part["type"] == "text" and (trimmed := trim_middle(part["text"], share)) is not None:
                        part["text"] = trimmed
                        trimmed_any = True
        stats.user_text_trimmed += trimmed_any
        encoded = _serialize(items)
        if budget.fits(encoded, len(items)) or share <= USER_TEXT_FLOOR_BYTES:
            break
        share = max(share * 9 // 10, USER_TEXT_FLOOR_BYTES)  # JSON escaping costs bytes the share did not count
    stats.over_budget = not budget.fits(encoded, len(items))
    return _finish(encoded, len(items), stats)


def trim_middle(text: str, max_bytes: int) -> str | None:
    """``text`` with its middle replaced by a marker so that head and tail together take
    ``max_bytes`` of UTF-8 (two thirds head); None when it already fits. Cuts fall on
    character boundaries."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return None
    head_len = max_bytes * 2 // 3
    tail_len = max_bytes - head_len
    head = raw[:head_len].decode("utf-8", errors="ignore")  # a cut character is dropped: floor
    tail = raw[len(raw) - tail_len :].decode("utf-8", errors="ignore") if tail_len else ""  # and ceil
    omitted = len(raw) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n[... {omitted} bytes omitted ...]\n{tail}"


def _omit_middle_steps(items: list[Item], keep_edge: int) -> tuple[list[Item], int]:
    """Every user item, the first and last ``keep_edge`` other items, and one marker for
    the steps between them."""
    steps = [i for i, item in enumerate(items) if item["type"] != "user"]
    if len(steps) <= keep_edge * 2:
        return items, 0
    first, last = steps[keep_edge], steps[len(steps) - keep_edge - 1]
    reduced: list[Item] = []
    omitted = 0
    marker: Item | None = None
    for i, item in enumerate(items):
        if item["type"] != "user" and first <= i <= last:
            omitted += 1
            if marker is None:
                marker = {"type": "assistant", "content": _STEPS_OMITTED, "tool_calls": []}
                reduced.append(marker)
            continue
        reduced.append(item)
    if marker is not None:
        marker["content"] = f"[... {omitted} intermediate steps omitted ...]"
    return reduced, omitted


def _strip_images(item: Item) -> int:
    parts = item.get("content") if item["type"] == "user" else item.get("images") if item["type"] == "tool_result" else None
    dropped = 0
    for i, part in enumerate(parts or ()):
        if part.get("type") == "image":
            parts[i] = {"type": "text", "text": _IMAGE}  # type: ignore[index]
            dropped += 1
    return dropped


def _copy(item: Item) -> Item:
    """A copy condensing may change without touching the caller's items."""
    copied = dict(item)
    for key in ("content", "images", "tool_calls"):
        if isinstance(copied.get(key), list):
            copied[key] = [dict(part) for part in copied[key]]
    return copied


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _size(encoded: str) -> int:
    return len(encoded.encode("utf-8"))


def _finish(encoded: str, items: int, stats: CondensationStats) -> CondensedTranscript:
    stats.final_bytes, stats.final_items = _size(encoded), items
    return CondensedTranscript(encoded, stats)
