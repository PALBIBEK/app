"""The per-process catalog cache and the dict <-> artifact converters.

``CompositionCatalog`` holds every artifact this process has seen; the
``catalog_artifacts`` table (``catalog.repository``) is the source of truth it syncs from.
"""

from supervisor.catalog.storage.composition import SYSTEM_OWNER, CompositionCatalog
from supervisor.catalog.storage.converters import (
    artifact_from_dict,
    artifact_to_dict,
    composition_from_dict,
    kind_of,
    memory_profile_from_dict,
    prompt_pack_from_dict,
    sandbox_profile_from_dict,
    tool_set_from_dict,
    with_digest,
    with_metadata,
    with_status,
)

__all__ = [
    "SYSTEM_OWNER",
    "CompositionCatalog",
    "artifact_from_dict",
    "artifact_to_dict",
    "composition_from_dict",
    "kind_of",
    "memory_profile_from_dict",
    "prompt_pack_from_dict",
    "sandbox_profile_from_dict",
    "tool_set_from_dict",
    "with_digest",
    "with_metadata",
    "with_status",
]
