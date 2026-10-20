"""Hands: the sandboxes a session's tools act in, kept apart from the brain.

Following "decouple the brain from the hands" (Anthropic, Scaling Managed
Agents), a sandbox is cattle, not a pet:

* **provisioned lazily**: a session's recipe is resolved (and validated) when the
  session is created, but the sandbox itself is built on the first tool call
  that needs one, so a turn that runs no such tool never waits for a container;
* **replaced, never nursed**: when a sandbox dies, the tool call that hit it gets
  a ``WORKSPACE_RESET`` result, the dead sandbox is let go, and the next call
  gets a fresh one hydrated from what was saved;
* **recorded**: ``HandProvisioned`` and ``HandLost`` in the session's journal say
  which sandbox served it and why each one ended.
"""

from supervisor.hands.recipe import HandRecipe
from supervisor.hands.service import HandEvents, SessionHands
from supervisor.hands.workspace import HandWorkspace

__all__ = ["HandEvents", "HandRecipe", "HandWorkspace", "SessionHands"]
