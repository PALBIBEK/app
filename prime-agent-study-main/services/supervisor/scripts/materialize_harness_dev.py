#!/usr/bin/env python3
"""Dev helper: materialize harness bind files for a composition into a workspace."""

from __future__ import annotations

import argparse
from pathlib import Path

from supervisor.catalog.binder import bind_runtime_seams
from supervisor.catalog.bootstrap import bootstrap_composition_catalog
from supervisor.catalog.resolve import resolve_composition

from supervisor.harness import HarnessCatalog, load_manifest, materialize_harness_binds


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize .yal/ for harness dev")
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--composition", default="coding-v1")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--tenant-id", default="dev")
    args = parser.parse_args()

    catalog_dir = args.catalog_dir.resolve()
    composition_catalog = bootstrap_composition_catalog(catalog_dir)
    pins = resolve_composition(composition_catalog, composition_id=args.composition)
    seams = bind_runtime_seams(pins, composition_catalog)

    harness_catalog = HarnessCatalog()
    versions_dir = catalog_dir / "versions"
    for path in sorted(versions_dir.glob("*.yaml")):
        manifest = load_manifest(path.read_text(encoding="utf-8"))
        harness_catalog.register(manifest, status="active")

    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    materialize_harness_binds(
        workspace,
        pins,
        seams,
        session_id="dev-session",
        tenant_id=args.tenant_id,
    )
    print(workspace / ".yal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
