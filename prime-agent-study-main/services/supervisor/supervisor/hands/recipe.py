"""How to build one session's sandbox: resolved once, used every time one is needed."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supervisor.catalog import NetworkAccess
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
    start: str | None = None
    """``with_run`` / ``on_first_tool`` (the sandbox profile's); None: the deployment default."""
    idle_stop_s: int | None = None
    """Stop after this long idle (0: never); None: the deployment default."""
    network: NetworkAccess | None = None
    """What the sandbox may reach (the profile's); None: the provider's default."""

    @property
    def provider_name(self) -> str:
        return self.provider.name

    async def preflight(self) -> None:
        """Raise if no sandbox could be built from this recipe (nothing is built)."""
        await self.provider.preflight(image=self.image)

    async def build(self) -> Sandbox:
        labels = dict(self.labels) or None
        if self.network is None:  # as before profiles named a network (providers of tests too)
            return await self.provider.create(image=self.image, labels=labels)
        return await self.provider.create(image=self.image, labels=labels, network=self.network)

    async def adopt(self, session_id: str) -> Sandbox | None:
        """The sandbox a dead worker left for ``session_id``, if it survived (F21)."""
        return await self.provider.adopt(session_id)
