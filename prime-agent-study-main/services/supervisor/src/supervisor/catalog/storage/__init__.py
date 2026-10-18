"""Catalog storage layer — persistence and conversion.

This module contains:
- converters.py: Dict <-> dataclass transformations
- memory.py: In-memory artifact and usage stores
- postgres.py: PostgreSQL artifact and usage stores
"""

from supervisor.catalog.storage.converters import (
    # From dict
    prompt_pack_from_dict,
    tool_set_from_dict,
    composition_from_dict,
    sandbox_profile_from_dict,
    memory_profile_from_dict,
    session_pins_from_dict,
    metadata_from_dict,
    artifact_from_dict,
    # To dict
    prompt_pack_to_dict,
    tool_set_to_dict,
    composition_to_dict,
    sandbox_profile_to_dict,
    memory_profile_to_dict,
    session_pins_to_dict,
    metadata_to_dict,
    artifact_to_dict,
    # Helpers
    kind_of,
    with_digest,
    with_status,
    with_metadata,
)

from supervisor.catalog.storage.memory import (
    InMemoryArtifactStore,
    InMemoryUsageStore,
    reject_floating_ref,
    validate_digest_format,
)

from supervisor.catalog.storage.postgres import (
    PostgresArtifactStore,
    PostgresUsageStore,
)

from supervisor.catalog.storage.composition import CompositionCatalog

__all__ = [
    # Composition catalog (sync, for bootstrap)
    "CompositionCatalog",
    # Converters - From dict
    "prompt_pack_from_dict",
    "tool_set_from_dict",
    "composition_from_dict",
    "sandbox_profile_from_dict",
    "memory_profile_from_dict",
    "session_pins_from_dict",
    "metadata_from_dict",
    "artifact_from_dict",
    # Converters - To dict
    "prompt_pack_to_dict",
    "tool_set_to_dict",
    "composition_to_dict",
    "sandbox_profile_to_dict",
    "memory_profile_to_dict",
    "session_pins_to_dict",
    "metadata_to_dict",
    "artifact_to_dict",
    # Converters - Helpers
    "kind_of",
    "with_digest",
    "with_status",
    "with_metadata",
    # Memory stores
    "InMemoryArtifactStore",
    "InMemoryUsageStore",
    "reject_floating_ref",
    "validate_digest_format",
    # Postgres stores
    "PostgresArtifactStore",
    "PostgresUsageStore",
]
