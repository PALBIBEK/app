"""The agent's memory tools: ``memory`` (long-term) and ``history`` (this session).

Ported from QM's agent tools. They run in the supervisor, not the sandbox, with
the session's own audience: reads may span every notebook the session may read,
writes only ever change its own notebook, and every result is plain text.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from supervisor.agent.events import Envelope
from supervisor.memory import history as session_history
from supervisor.memory.binding import MemoryBinding
from supervisor.memory.client import MemoryRefused, MemoryUnavailable
from supervisor.memory.cross import read_notebook, search_across
from supervisor.memory.disclosure import Audience
from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory_service.notebooks.errors import MemoryDisclosureDenied, MemoryFactsMissing
from supervisor.memory_service.notebooks.types import CaptureMetadata, Edit, Provenance

MEMORY_TOOL: Final = "memory"
HISTORY_TOOL: Final = "history"
SESSIONS_TOOL: Final = "sessions"

_MEMORY_DESCRIPTION: Final = (
    "Your durable memory of the person you work for — the ONE way to read or change it. "
    "It is NOT a file: writing MEMORY.md with files or the shell does not touch it. It persists "
    "across every conversation with this agent (continuity — you're a colleague who remembers, "
    "not a fresh chat each time). Reads may span authorized notebooks; writes only change this "
    "conversation's own notebook. "
    'action="search" finds remembered facts matching every word of `query` (case-insensitive) '
    "across every notebook this conversation may read — check what you already know before "
    "asking. Only a bounded selection of memories is loaded into context; search the full "
    "notebooks for facts missing from that selection. Keep memory concise. Save pointers to data, "
    "never the data itself — working state (queues, backlogs, watermarks, ID lists, logs, "
    "per-item status) belongs in a file written with the write tool, with at most one memory "
    "line naming that file and what it holds. If a fact is a list that grows, it's a file. "
    'action="add" saves new `facts` ([{text}, …]) now — short, self-contained facts (a '
    "preference, an identifier, an ongoing project, how they like to work); never secrets, "
    "credentials, one-off trivia, or anything already recorded somewhere you can look up. "
    'action="read" lists this conversation\'s notebook, each fact with its #id; pass '
    "`notebook` from a search result to read another authorized notebook. "
    'action="edit" replaces the text of existing facts (`facts`: [{id, text}, …]); '
    'action="delete" forgets facts by `ids`. Fix a fact that changed instead of adding a '
    "contradicting one, and delete what is wrong or stale. Ids come from search or read; an edit "
    "or delete that names any id not in this conversation's notebook changes nothing and says "
    "which ids were bad."
)

_HISTORY_DESCRIPTION: Final = (
    "Read THIS conversation's own durable transcript — every past turn, tool call and tool "
    "result, including parts compacted out of your current context (a compaction summary names "
    "the entries it replaced). Use it when something earlier in this conversation is referenced "
    'but not in front of you ("that file from last week", "the output of that command"). '
    "Distinct from `memory`, which holds remembered facts across conversations; `history` reads "
    "only this one, verbatim. Entries are tagged type#seq with a timestamp. One mode per call: "
    "`query` searches messages and tool calls (not results), newest first, all terms must match; "
    "`seq` opens one entry in full, a tool result too; `from_seq` (with optional `to_seq`) reads a "
    "range in order, each entry clipped (open one for all of it); `call_id` returns one tool call "
    "with its result, in full."
)

MEMORY_SCHEMA: Final[dict[str, Any]] = {
    "type": "function",
    "function": {
        "name": MEMORY_TOOL,
        "description": _MEMORY_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "read", "add", "edit", "delete"],
                    "description": "search facts, read this notebook, or add, edit, or delete facts.",
                },
                "query": {
                    "type": "string",
                    "description": "search only: words to look for among your remembered facts "
                    "(all must match).",
                },
                "limit": {
                    "type": "integer",
                    "description": "search only: max facts to return (default 20).",
                },
                "notebook": {
                    "type": "string",
                    "description": "search/read only: exact notebook id from a search result. Omit "
                    "to search all authorized notebooks or read your own.",
                },
                "facts": {
                    "type": "array",
                    "description": "add: [{text}, …]; edit: [{id, text}, …].",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "edit only: the fact's id, e.g. #42.",
                            },
                            "text": {
                                "type": "string",
                                "description": "one short self-contained fact — a pointer to data, "
                                "never the data itself.",
                            },
                        },
                        "required": ["text"],
                    },
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "delete only: ids of the facts to forget.",
                },
            },
            "required": ["action"],
        },
    },
}

HISTORY_SCHEMA: Final[dict[str, Any]] = {
    "type": "function",
    "function": {
        "name": HISTORY_TOOL,
        "description": _HISTORY_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to look for in this conversation's transcript (all must "
                    "match).",
                },
                "seq": {
                    "type": "integer",
                    "description": "Reopen the single entry with this seq in full, instead of "
                    "searching.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 20, query mode only).",
                },
                "from_seq": {
                    "type": "integer",
                    "description": "Read the entries from this seq on, oldest first (bounded; it "
                    "says where to continue).",
                },
                "to_seq": {
                    "type": "integer",
                    "description": "With from_seq: the last seq to read (inclusive).",
                },
                "call_id": {
                    "type": "string",
                    "description": "A tool call's id: returns that call and its result in full.",
                },
            },
        },
    },
}

_SESSIONS_DESCRIPTION: Final = (
    "Your other conversations with this person, for this exact agent version. "
    'action="list" shows recent sessions (id, title, status), newest first. '
    'action="read" returns a session\'s recent transcript (user messages and final replies only) '
    "by `session_id`. "
    'action="new" starts a fresh session whose first message is `text`; action="fork" copies '
    "this conversation into a new session (optionally titled `title`). Use these only when the "
    "person asks; never to hand off work silently."
)

SESSIONS_SCHEMA: Final[dict[str, Any]] = {
    "type": "function",
    "function": {
        "name": SESSIONS_TOOL,
        "description": _SESSIONS_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read", "new", "fork"]},
                "session_id": {"type": "string", "description": "read only: the session to read."},
                "limit": {
                    "type": "integer",
                    "description": "read only: max entries (default 50, at most 200).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "read only: max characters returned (default 8000).",
                },
                "text": {"type": "string", "description": "new only: the first message."},
                "title": {"type": "string", "description": "new/fork only: an optional title."},
            },
            "required": ["action"],
        },
    },
}

UNAVAILABLE: Final = "[memory isn't available in this conversation]"


@dataclass(frozen=True, slots=True)
class ToolOutput:
    text: str
    is_error: bool = False


type SessionsTool = Callable[[Mapping[str, Any]], Awaitable[ToolOutput]]


@dataclass(slots=True)
class TurnMemory:
    """What one session's tool calls need: its memory, audience and visible history."""

    memory: MemoryService | None
    binding: MemoryBinding | None
    audience: Audience | None
    session_id: str
    events: Sequence[Envelope] = ()
    # Provenance of everything this turn's context depends on (None: untracked).
    dependencies: tuple[Provenance, ...] | None = None
    sessions: SessionsTool | None = None
    labels: Mapping[str, str] = field(default_factory=dict)

    def schemas(self) -> list[dict[str, Any]]:
        out = [HISTORY_SCHEMA]
        if self.memory is not None and self.binding is not None:
            out.insert(0, MEMORY_SCHEMA)
        if self.sessions is not None:
            out.append(SESSIONS_SCHEMA)
        return out

    def handles(self, name: str) -> bool:
        return name in {s["function"]["name"] for s in self.schemas()}

    async def call(self, name: str, args: Mapping[str, Any]) -> ToolOutput:
        if name == HISTORY_TOOL:
            return self._history(args)
        if name == SESSIONS_TOOL and self.sessions is not None:
            return await self.sessions(args)
        if name == MEMORY_TOOL:
            try:
                return await self._memory(args)
            except (MemoryFactsMissing, MemoryDisclosureDenied) as exc:
                return ToolOutput(f"[error] {exc}", True)
            except (MemoryUnavailable, MemoryRefused) as exc:
                return ToolOutput(f"[error] long-term memory is unavailable right now: {exc}", True)
        return ToolOutput(f"[error] unknown tool {name!r}", True)

    # --- memory ----------------------------------------------------------------

    def _label(self, notebook_id: str) -> str:
        return self.labels.get(notebook_id) or (self.binding.labels if self.binding else {}).get(
            notebook_id, notebook_id
        )

    async def _memory(self, args: Mapping[str, Any]) -> ToolOutput:
        action = str(args.get("action") or "")
        memory, binding, audience = self.memory, self.binding, self.audience
        if memory is None or binding is None or audience is None:
            return ToolOutput(UNAVAILABLE, True)
        notebook = args.get("notebook")
        if notebook is not None and action not in ("search", "read"):
            return ToolOutput(
                "[error] notebook is only allowed for memory search/read; writes stay in this "
                "conversation.",
                True,
            )
        facts = [
            (str(f.get("id") or "").strip(), str(f.get("text") or "").strip())
            for f in args.get("facts") or []
            if isinstance(f, Mapping)
        ]
        match action:
            case "search":
                return await self._search(memory, binding, args, notebook)
            case "read":
                return await self._read(memory, binding, notebook)
            case "add":
                texts = [text for _, text in facts if text]
                if not texts:
                    return ToolOutput(
                        "[error] memory add requires `facts` (a non-empty list of {text}).", True
                    )
                if binding.writable is None:
                    return ToolOutput(UNAVAILABLE, True)
                added = await memory.capture(
                    binding.writable,
                    texts,
                    author=audience.actor,
                    context=CaptureContext(
                        mode="explicit",
                        actor=audience.actor,
                        source=audience.target,
                        session_id=self.session_id,
                        inherited=self.dependencies,
                    ),
                )
                if not added:
                    return ToolOutput("Already remembered — nothing new to save.")
                return ToolOutput(f"Remembered {added} fact{'' if added == 1 else 's'}.")
            case "edit":
                if not facts or any(not i or not t for i, t in facts):
                    return ToolOutput(
                        "[error] memory edit requires `facts` as a non-empty list of {id, text}.",
                        True,
                    )
                if binding.writable is None:
                    return ToolOutput(UNAVAILABLE, True)
                await memory.edit(
                    binding.writable,
                    [Edit(i, t) for i, t in facts],
                    author=audience.actor,
                    context=CaptureMetadata(
                        source=audience.target,
                        session_id=self.session_id,
                        inherited=self.dependencies,
                    ),
                )
                return ToolOutput(f"Updated {len(facts)} fact{'' if len(facts) == 1 else 's'}.")
            case "delete":
                ids = [str(i).strip() for i in args.get("ids") or [] if str(i).strip()]
                if not ids:
                    return ToolOutput(
                        "[error] memory delete requires `ids` (a non-empty list).", True
                    )
                if binding.writable is None:
                    return ToolOutput(UNAVAILABLE, True)
                await memory.delete(binding.writable, ids, author=audience.actor)
                return ToolOutput(f"Forgot {len(ids)} fact{'' if len(ids) == 1 else 's'}.")
            case _:
                return ToolOutput(f"[error] unknown memory action {action!r}.", True)

    async def _search(
        self, memory: MemoryService, binding: MemoryBinding, args: Mapping[str, Any], notebook: Any
    ) -> ToolOutput:
        query = str(args.get("query") or "")
        if not query.strip():
            return ToolOutput("[error] memory search requires `query`.", True)
        readable = binding.readable
        if not readable or (notebook is not None and notebook not in readable):
            return ToolOutput(UNAVAILABLE, True)
        targets = [notebook] if notebook is not None else list(readable)
        limit = int(args.get("limit") or 20)
        hits = await search_across(memory, targets, query, limit)
        if not hits:
            return ToolOutput(f'[no remembered facts match "{query}"]')
        tag = len(readable) > 1 or notebook is not None
        return ToolOutput("\n".join(f"[{nb}] {fact}" if tag else fact for nb, fact in hits))

    async def _read(
        self, memory: MemoryService, binding: MemoryBinding, notebook: Any
    ) -> ToolOutput:
        target = notebook or binding.write
        if target is None or target not in binding.readable:
            return ToolOutput(UNAVAILABLE, True)
        body = await read_notebook(memory, target)
        if not body.strip():
            body = "(you have nothing remembered here yet)"
        return ToolOutput(f"### {self._label(target)}\n{body}" if notebook is not None else body)

    # --- history ---------------------------------------------------------------

    def _history(self, args: Mapping[str, Any]) -> ToolOutput:
        if (call_id := args.get("call_id")) is not None:
            found = session_history.call_entries(self.events, str(call_id))
            return ToolOutput(found or f"[no tool call {call_id!r} in this conversation]")
        if (start := args.get("from_seq")) is not None:
            end = args.get("to_seq")
            if not _is_int(start) or (end is not None and not _is_int(end)):
                return ToolOutput("[error] history `from_seq` and `to_seq` must be integers.", True)
            listed = session_history.slice_entries(self.events, start, end)
            return ToolOutput(listed or f"[no entries from #{start} in this conversation]")
        seq = args.get("seq")
        if seq is not None:
            if not isinstance(seq, int) or isinstance(seq, bool):
                return ToolOutput(
                    "[error] history `seq` must be an integer entry number, like the 87 in "
                    "tool_call#87.",
                    True,
                )
            entry = session_history.open_entry(self.events, seq)
            return ToolOutput(entry or f"[no entry #{seq} in this conversation]")
        query = str(args.get("query") or "")
        if not query.strip():
            return ToolOutput("[error] history requires `query` or `seq`.", True)
        hits = session_history.search(self.events, query, int(args.get("limit") or 20))
        return ToolOutput(
            "\n".join(hits) if hits else f'[nothing in this conversation matches "{query}"]'
        )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


PLATFORM_TOOLS: Final = frozenset({MEMORY_TOOL, HISTORY_TOOL, SESSIONS_TOOL})
