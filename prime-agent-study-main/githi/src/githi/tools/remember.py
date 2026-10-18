"""remember -- the agent's write path into memory.

Without this, memory can only be filled by whoever configured the harness,
which makes it configuration rather than memory. The point is that a
preference stated in today's session applies in tomorrow's.

**Model-driven, not automatic.** Deciding on the agent's behalf what deserves
remembering is the fitness-signal problem: there is no ground truth for
"was that worth keeping", and both Grok and Hermes turn out to have no
outcome-based signal for it either. A tool makes the judgement explicit,
visible in the transcript, and reversible -- the file is markdown, so a bad
memory can be deleted with a text editor.

**The description does most of the work.** A tool the model cannot tell when
to reach for goes unused, and one it reaches for constantly fills memory with
noise that crowds out the useful entries. So it names both what belongs
(durable preferences, conventions, corrections) and what does not (anything
that will be false next week).
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolResult, fail, ok

DESCRIPTION = (
    "Save a durable fact to long-term memory, available in all future sessions. "
    "Use it for the user's stated preferences, project conventions, corrections "
    "they have given you, and hard-won lessons about their tools or workflow. "
    "Do NOT use it for things that change -- version numbers, issue counts, "
    "today's state of a branch -- because a stale memory is worse than none: it "
    "is confidently wrong and nothing will contradict it. State the fact so it "
    "makes sense on its own, months from now, to someone without this "
    "conversation."
)


def remember_tool(memory: Any) -> Tool:
    def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        fact = str(args.get("fact") or "").strip()
        if not fact:
            return fail("remember requires a `fact` to store.")

        if memory is None:
            # Explicit rather than a silent no-op: a tool that reports success
            # while storing nothing teaches the model to trust a memory that
            # will be empty next session.
            return fail("no memory is configured for this session, so nothing "
                        "can be stored.")

        try:
            memory.remember(fact)
        except Exception as exc:  # noqa: BLE001 - the model can react to a message
            return fail(f"could not store the fact: {exc}")

        return ok(f"stored in memory: {fact}", fact=fact)

    return Tool(
        name="remember",
        description=DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "the fact to remember, phrased to stand alone",
                },
            },
            "required": ["fact"],
        },
        run=run,
    )
