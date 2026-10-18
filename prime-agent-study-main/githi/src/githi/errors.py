"""Every exception this package raises on purpose.

One shared base so the CLI can distinguish "githi decided to stop" from a
genuine bug: `except GithiError` prints a message, anything else keeps its
traceback because it was never anticipated.
"""

from __future__ import annotations


class GithiError(Exception):
    """Base for all deliberate failures."""


class ToolNotFound(GithiError):
    def __init__(self, name: str) -> None:
        super().__init__(f"no tool named {name!r}")
        self.name = name


class ToolError(GithiError):
    """A tool failed in a way the model should see and may retry."""


class SandboxViolation(GithiError):
    """A tool tried to leave the sandbox.

    Distinct from ToolError: the model gets told, but this is a policy
    decision by the harness, not a malfunction of the tool.
    """


class LLMError(GithiError):
    """The provider call failed."""


class ConfigError(GithiError):
    """Missing or invalid configuration -- usually an absent API key."""


class AskUserError(GithiError):
    """Human-question failures with a stable code (DeepSeek UserQuestionError).

    Codes:
    - NO_PROVIDER — no UI asker registered
    - DELEGATED_CALLER — child owned by another live agent must not ask
    - EMPTY_QUESTIONS / BAD_ARGS — bad tool arguments
    - ASK_ABORTED — interrupt / Ctrl-C before or during the wait
    - ASK_CANCELLED — user quit the prompt (EOF), not an interrupt
    - BAD_ANSWER — UI returned a shape/labels that do not match the questions
    - UI_ERROR — unexpected exception from the UI
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code
