"""Catalog storage layer — persistence and conversion.

This module contains:
- converters.py: Dict <-> dataclass transformations
- memory.py: In-memory artifact and usage stores
- postgres.py: PostgreSQL artifact and usage stores
"""

from supervisor.catalog.storage.composition import CompositionCatalog
from supervisor.catalog.storage.converters import (
    artifact_from_dict,
    artifact_to_dict,
    composition_from_dict,
    composition_to_dict,
    kind_of,
    memory_profile_from_dict,
    memory_profile_to_dict,
    metadata_from_dict,
    metadata_to_dict,
    prompt_pack_from_dict,
    prompt_pack_to_dict,
    sandbox_profile_from_dict,
    sandbox_profile_to_dict,
    tool_set_from_dict,
    tool_set_to_dict,
    with_digest,
    with_status,
)
from supervisor.catalog.storage.memory import reject_floating_ref, validate_digest_format

__all__ = [
    "CompositionCatalog",
    "artifact_from_dict",
    "artifact_to_dict",
    "composition_from_dict",
    "composition_to_dict",
    "kind_of",
    "memory_profile_from_dict",
    "memory_profile_to_dict",
    "metadata_from_dict",
    "metadata_to_dict",
    "prompt_pack_from_dict",
    "prompt_pack_to_dict",
    "reject_floating_ref",
    "sandbox_profile_from_dict",
    "sandbox_profile_to_dict",
    "tool_set_from_dict",
    "tool_set_to_dict",
    "validate_digest_format",
    "with_digest",
    "with_status",
]
