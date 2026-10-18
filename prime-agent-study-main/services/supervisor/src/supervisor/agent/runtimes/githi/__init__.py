"""
Githi agent runtime — in-process AgentRuntime for the githi harness.

Phase 1.5: platform AgentRuntime protocol + AgentLoop wiring.
Factory name: ``githi``.
"""

from .loop import AgentLoop, AgentLoopError
from .runtime import GithiRuntime

__all__ = [
    "AgentLoop",
    "AgentLoopError",
    "GithiRuntime",
]
