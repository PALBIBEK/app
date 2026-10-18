"""Session storage: an append-only JSONL file holding a **tree**.

One JSON object per line, appended as each entry arrives. A crash therefore
leaves every prior entry intact and readable, which a single re-serialised
JSON document would not: that would be truncated mid-array and unparseable in
its entirety, losing the whole run rather than its last message.

**Why a tree and not a list.** Agent runs go wrong in the middle. With a flat
log an agent that took a bad turn at step 5 leaves two options: keep the bad
context, or discard all five steps and start over. Each entry recording its
parent means you can fork from step 4, try a different approach, and still
have the failed branch on disk to compare against -- without paying again for
the four steps that were fine.

Appending is still strictly append-only. A fork writes a new entry whose
parent is an older one; nothing is ever rewritten, so the crash-safety
property above is unchanged and two processes appending cannot corrupt each
other's lines.

**Legacy files keep working.** Sessions written before entries had ids are
bare message objects. They are read as a single linear branch -- which is
exactly what they are -- so no existing session file becomes unreadable.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..types import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


@dataclass
class SessionEntry:
    """One line of the file. `parent_id is None` marks a root."""

    id: str
    parent_id: str | None
    type: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> Message | None:
        return _decode(self.payload) if self.type == "message" else None


class Session:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._leaf: str | None = None
        """The entry the next append will hang from. Resolved lazily from the
        file so reopening a session resumes where it left off."""

    # ---------------- writing ----------------

    def append(self, message: Message) -> str:
        """Append a message and return its entry id (a fork point)."""
        return self._write("message", _encode(message))

    def extend(self, messages: list[Message]) -> None:
        for message in messages:
            self.append(message)

    def record_model_change(self, model: str) -> str:
        return self._write("model_change", {"model": model})

    def record_thinking_level_change(self, level: str) -> str:
        return self._write("thinking_level_change", {"level": level})

    def record_active_tools_change(self, tool_names: list[str]) -> str:
        return self._write("active_tools_change", {"tool_names": list(tool_names)})

    def record_compaction(self, summary: str = "",
                          messages: list[Message] | None = None) -> str:
        """Mark where compaction happened, keeping the summary alongside.

        This is what makes compaction non-destructive: the model is shown the
        summary while the branch still holds everything it replaced.
        """
        payload: dict[str, Any] = {"summary": summary}
        if messages is not None:
            payload["messages"] = [_encode(message) for message in messages]
        return self._write("compaction", payload)

    def record_branch_summary(self, summary: str) -> str:
        """What an abandoned branch established, carried onto this one.

        An entry rather than a message: it is provenance, so `load` skips it
        and the model never reads it as something the user said. The branch it
        describes stays on disk, so the summary is an index into the original
        rather than a replacement for it.
        """
        return self._write("branch_summary", {"summary": summary})

    def record_label(self, text: str) -> str:
        """Name a point worth coming back to, so a fork target can be found
        later by meaning rather than by counting entries."""
        return self._write("label", {"text": text})

    def _write(self, entry_type: str, payload: dict[str, Any]) -> str:
        entry = SessionEntry(
            id=uuid.uuid4().hex,
            parent_id=self._current_leaf(),
            type=entry_type,
            timestamp=datetime.now(timezone.utc).isoformat(),
            payload=payload,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "id": entry.id, "parent_id": entry.parent_id, "type": entry.type,
                "timestamp": entry.timestamp, "payload": entry.payload,
            }) + "\n")
        self._leaf = entry.id
        return entry.id

    # ---------------- branching ----------------

    def branch_from(self, entry_id: str) -> None:
        """Point subsequent appends at `entry_id`, forking the tree there.

        An unknown id raises rather than starting a fresh root: silently
        rooting a new branch would strand the existing transcript somewhere
        the caller has no reason to look for it.
        """
        if entry_id not in {e.id for e in self.entries()}:
            raise KeyError(f"no session entry with id {entry_id!r}")
        self._leaf = entry_id

    def leaves(self) -> list[SessionEntry]:
        """Every branch tip -- entries no other entry claims as parent."""
        entries = self.entries()
        claimed = {e.parent_id for e in entries if e.parent_id}
        return [e for e in entries if e.id not in claimed]

    # ---------------- reading ----------------

    def entries(self) -> list[SessionEntry]:
        """Every entry in the file, in write order, across all branches."""
        if not self.path.exists():
            return []

        out: list[SessionEntry] = []
        previous_id: str | None = None
        for index, line in enumerate(self.path.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                # A half-written final line is the expected shape of a crash.
                # Losing that one entry is acceptable; refusing to load the
                # session because of it is not.
                continue

            entry = _read_entry(raw, previous_id, index)
            if entry is None:
                continue
            out.append(entry)
            previous_id = entry.id
        return out

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        """The path from the root to `from_id` (default: the current leaf).

        Walks parent links upward and reverses, so the result is in
        conversation order and contains only this branch -- entries from a
        sibling branch are invisible, which is the whole point.
        """
        entries = self.entries()
        if not entries:
            return []

        by_id = {e.id: e for e in entries}
        cursor = from_id or self._current_leaf()
        if cursor is None:
            return []

        path: list[SessionEntry] = []
        seen: set[str] = set()
        while cursor and cursor in by_id:
            if cursor in seen:                 # pragma: no cover - corrupt file
                break                          # a parent cycle must not hang the load
            seen.add(cursor)
            entry = by_id[cursor]
            path.append(entry)
            cursor = entry.parent_id
        return list(reversed(path))

    def load(self, from_id: str | None = None) -> list[Message]:
        """The conversation on one branch.

        Non-message entries are skipped: a model change is provenance, not
        something to replay at the model.
        """
        messages: list[Message] = []
        for entry in self.get_branch(from_id):
            if entry.type == "message" and entry.message is not None:
                messages.append(entry.message)
            elif entry.type == "compaction":
                snapshot = entry.payload.get("messages")
                if isinstance(snapshot, list):
                    messages = [message for raw in snapshot
                                if (message := _decode(raw)) is not None]
                elif entry.payload.get("summary"):
                    messages.append(UserMessage(str(entry.payload["summary"])))
        return messages

    def _current_leaf(self) -> str | None:
        if self._leaf is None:
            entries = self.entries()
            self._leaf = entries[-1].id if entries else None
        return self._leaf


def _read_entry(raw: dict, previous_id: str | None,
                index: int) -> SessionEntry | None:
    """Parse one line, in either the tree format or the legacy flat one.

    A legacy line is a bare message with no id. It is given a synthetic id
    derived from its **line number** and chained to the line before it, which
    reads the old file as one linear branch -- an accurate description of what
    it always was.

    The id has to be derived rather than generated: a random one would differ
    on every read, so a leaf recorded from one call would dangle by the next
    and the branch would come back empty.
    """
    if "id" in raw and "type" in raw:
        return SessionEntry(
            id=raw["id"], parent_id=raw.get("parent_id"), type=raw["type"],
            timestamp=raw.get("timestamp", ""), payload=raw.get("payload") or {})

    if "role" not in raw:
        return None
    return SessionEntry(id=f"legacy-{index}", parent_id=previous_id,
                        type="message", timestamp="", payload=raw)


def _encode(message: Message) -> dict:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        return {"role": "assistant", "content": message.content,
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                               for c in message.tool_calls]}
    if isinstance(message, ToolResultMessage):
        return {"role": "tool", "tool_call_id": message.tool_call_id,
                "content": message.content, "is_error": message.is_error}
    raise TypeError(f"cannot serialise {type(message).__name__}")


def _decode(raw: dict) -> Message | None:
    role = raw.get("role")
    if role == "system":
        return SystemMessage(raw.get("content", ""))
    if role == "user":
        return UserMessage(raw.get("content", ""))
    if role == "assistant":
        return AssistantMessage(
            content=raw.get("content", ""),
            tool_calls=[ToolCall(id=c["id"], name=c["name"],
                                 arguments=c.get("arguments") or {})
                        for c in raw.get("tool_calls") or []],
        )
    if role == "tool":
        return ToolResultMessage(tool_call_id=raw["tool_call_id"],
                                 content=raw.get("content", ""),
                                 is_error=bool(raw.get("is_error")))
    return None
