"""Long-term memory: named notebooks of facts per principal per pinned agent version.

Modelled on YC QM's notebook memory. Entry point: ``MemoryPlatform``; see
``services/supervisor/MEMORY.md`` for the design and its mapping onto QM.
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
