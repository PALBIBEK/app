"""How to build one session's sandbox: resolved once, used every time one is needed."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.sandbox.base import Sandbox, SandboxProvider


@dataclass(frozen=True, slots=True)
class HandRecipe:
    """The pinned provider, image and labels for a session's sandboxes.

    Resolving it (``resolve_sandbox_create``) checks the harness, the sandbox
    profile and multi-tenant policy, so a session that can never get a sandbox is
    refused at creation, even though the sandbox itself is built later.
    """

    provider: SandboxProvider
    image: str | None
    labels: Mapping[str, str] = field(default_factory=dict)

    @property
    def provider_name(self) -> str:
        return self.provider.name

    async def preflight(self) -> None:
        """Raise if no sandbox could be built from this recipe (nothing is built)."""
        await self.provider.preflight(image=self.image)

    async def build(self) -> Sandbox:
        return await self.provider.create(image=self.image, labels=dict(self.labels) or None)

    async def adopt(self, session_id: str) -> Sandbox | None:
        """The sandbox a dead worker left for ``session_id``, if it survived (F21)."""
        return await self.provider.adopt(session_id)
