"""One object describing everything a tool is allowed to do.

Pi has no equivalent -- its README states plainly that it ships no permission
system and runs with the full authority of the user who launched it, deferring
isolation to a container. That is a coherent choice for a local coding
assistant a human is watching. It is the wrong default for an agent running
unattended against someone else's repository, which is what this harness is
for. Hence: a policy object, restrictive by default, that every tool consults.

Keeping it in one dataclass is deliberate. Security properties spread across
eight tool implementations cannot be reviewed; a reader here sees the entire
blast radius at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_S = 30
MAX_TIMEOUT_S = 900
"""Fifteen minutes.

Was 120s, chosen because it felt like a safe number rather than because any
real workload was measured against it. That was wrong in a way that is
invisible from inside the agent: a full `git clone` of a large repository
takes ~190s, so every attempt died at the ceiling, and the model -- correctly
inferring that cloning was the right approach and incorrectly inferring that
it was simply too slow -- fell back to `--depth 100`. Shallow history cannot
answer "which commit introduced this line", and `git blame` against it
silently misattributes to the oldest grafted commit, so the run produced a
confident wrong answer.

Pi imposes no default timeout at all and cloned the same repo without
incident. A ceiling is still worth having here (githi runs unattended, and a
hung command with no bound hangs the whole run), but it has to be set from
what real work costs, not from what sounds prudent."""
DEFAULT_MAX_OUTPUT_BYTES = 20_000

_SECRET_SUBSTRINGS = ("TOKEN", "API_KEY", "APIKEY", "SECRET", "PASSWORD",
                      "CREDENTIAL", "PRIVATE_KEY", "SESSION_KEY", "ACCESS_KEY")
_SECRET_PREFIXES = ("GITHUB_", "GH_", "OPENROUTER_", "OPENAI_", "ANTHROPIC_",
                    "GOOGLE_", "GEMINI_", "AWS_", "AZURE_", "GROQ_", "XAI_",
                    "MISTRAL_", "HF_", "COPILOT_", "LITELLM_", "CEREBRAS_",
                    "DEEPSEEK_", "MINIMAX_", "ZAI_")


@dataclass
class SandboxPolicy:
    """Restrictive by default. Every permissive setting is opt-in.

    The default matters more than the options: anyone who forgets to think
    about the policy gets the safe one, and the unsafe configurations are
    things somebody had to type on purpose.
    """

    root: Path
    """Working directory, and the boundary for every file operation."""

    writable: bool = True
    """Whether file-mutating tools may run at all. Reads are always allowed
    within `root`."""

    allow_network: bool = False
    """Advisory: consulted by tools that would reach the network. NOT
    enforced at the kernel level for arbitrary shell commands -- see
    `runner.run_command`'s docstring for exactly what is and is not
    guaranteed."""

    allow_credentials: bool = False
    """When False (the default) the child process environment is stripped of
    anything that looks like a credential. This is the actual enforcement
    mechanism behind `allow_network` for shell commands: a command can still
    reach the internet, but with nothing to authenticate as."""

    timeout_s: int = DEFAULT_TIMEOUT_S
    max_timeout_s: int = MAX_TIMEOUT_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    timeout_was_clamped: bool = field(default=False, init=False)
    """Whether the requested timeout was reduced to the ceiling.

    Exists so the reduction can be *reported*. Silently clamping is what made
    the failure above so hard to diagnose: the caller asked for 180s, got
    120s, and was never told -- so a killed command looked like slow work
    rather than an overridden request, and the model debugged the wrong
    problem."""

    extra_secrets: tuple[str, ...] = field(default_factory=tuple)
    """Additional environment variable names to treat as secret, for
    deployment-specific credentials the generic patterns would miss."""

    def __post_init__(self) -> None:
        # Resolved once, here, because every path comparison downstream
        # depends on it being absolute and symlink-free.
        self.root = Path(self.root).resolve()
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        if self.timeout_s > self.max_timeout_s:
            self.timeout_was_clamped = True
            self.timeout_s = self.max_timeout_s

    def is_secret(self, name: str) -> bool:
        """Pattern match, never a fixed denylist.

        A denylist of known variable names is correct exactly until someone
        adds a new credential to the environment, at which point it leaks
        silently and nothing fails. Matching on shape means an unknown future
        `SOME_NEW_PROVIDER_API_KEY` is caught the first time it appears.
        """
        if self.allow_credentials:
            return False
        upper = name.upper()
        if upper in {s.upper() for s in self.extra_secrets}:
            return True
        if any(upper.startswith(p) for p in _SECRET_PREFIXES):
            return True
        return any(s in upper for s in _SECRET_SUBSTRINGS)
