"""Every composition the repository ships passes verification.

Boot soft-skips a composition that fails (it is marked deprecated, with only a
log line), so a broken shipped composition would otherwise go unnoticed until
someone tried to start a session on it.
"""

from __future__ import annotations

from supervisor.catalog.loading.bootstrap import (
    default_catalog_dir,
    load_composition_catalog,
    resolve_catalog_dir,
    verify_composition_catalog,
)


def test_no_shipped_composition_is_soft_skipped_at_boot() -> None:
    root = resolve_catalog_dir(default_catalog_dir())
    assert verify_composition_catalog(load_composition_catalog(root), root) == []
