"""When a session's sandbox starts and stops: the deployment's defaults, which a sandbox
profile (an *environment* in the builder) overrides.

The shape follows Claude Managed Agents: the brain is not kept waiting for its hands (the
sandbox starts building as a run begins, while the model works on its first step) and idle
hands cost nothing (an idle sandbox is saved and stopped; the next tool call restores it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from supervisor.hands.recipe import HandRecipe

STARTS: Final = ("with_run", "on_first_tool")


@dataclass(frozen=True, slots=True)
class SandboxLifecycle:
    start: str = "on_first_tool"
    """``SANDBOX_START``: for profiles that do not say. ``on_first_tool`` keeps sessions that
    never run a tool free of containers; environments made in the builder start ``with_run``."""
    idle_stop_s: int = 900
    """``SANDBOX_IDLE_STOP_S``: stop a sandbox idle this long, for profiles that do not say
    (0: never)."""
    sweep_s: float = 30.0
    """``SANDBOX_IDLE_SWEEP_S``: how often a worker looks for idle sandboxes."""

    @classmethod
    def from_env(cls) -> SandboxLifecycle:
        start = os.environ.get("SANDBOX_START", "on_first_tool").strip() or "on_first_tool"
        if start not in STARTS:
            raise ValueError(f"SANDBOX_START={start!r}: use one of {', '.join(STARTS)}")
        return cls(
            start=start,
            idle_stop_s=int(os.environ.get("SANDBOX_IDLE_STOP_S", "900")),
            sweep_s=float(os.environ.get("SANDBOX_IDLE_SWEEP_S", "30")),
        )

    def starts_with_run(self, recipe: HandRecipe | None) -> bool:
        if recipe is None:
            return False
        return (recipe.start or self.start) == "with_run"

    def idle_limit(self, recipe: HandRecipe | None) -> int:
        """Seconds idle before a stop; 0 never."""
        if recipe is None:
            return 0
        return self.idle_stop_s if recipe.idle_stop_s is None else recipe.idle_stop_s
