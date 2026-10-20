"""Long-term memory: named notebooks of facts per principal per pinned agent version.

Modelled on YC QM's notebook memory. Notebooks are stored by the memory service
(``supervisor.memory_service.notebooks``); this package is the supervisor's side:
which notebooks a session binds, who may see which fact (disclosure), how writes
are classified, and recall, tools and capture inside turns. Entry point:
``MemoryPlatform``; the memory service is reached through ``memory.client``.
"""

from supervisor.memory.binding import MemoryBinding, MemoryRequest, agent_pin
from supervisor.memory.disclosure import Audience
from supervisor.memory.platform import MemoryPlatform
from supervisor.memory.service import CaptureContext, MemoryService
from supervisor.memory_service.notebooks.types import Fact, Notebook, Provenance, Sensitivity

__all__ = [
    "Audience",
    "CaptureContext",
    "Fact",
    "MemoryBinding",
    "MemoryPlatform",
    "MemoryRequest",
    "MemoryService",
    "Notebook",
    "Provenance",
    "Sensitivity",
    "agent_pin",
]
