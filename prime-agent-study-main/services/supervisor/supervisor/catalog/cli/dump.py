"""Dump offered composition catalog (offline parity with boot)."""

from __future__ import annotations

import argparse
import json
import sys

from supervisor.catalog.loading.bootstrap import bootstrap_composition_catalog, resolve_catalog_dir
from supervisor.catalog.loading.verify import dump_composition_catalog


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dump YAL composition catalog digests")
    parser.add_argument(
        "--catalog-dir",
        default=None,
        help="Catalog root (default: YAL_CATALOG_DIR or shipped harness/)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip parity verify (dump only)",
    )
    args = parser.parse_args(argv)
    catalog_dir = resolve_catalog_dir(args.catalog_dir)
    catalog = bootstrap_composition_catalog(
        catalog_dir, fail_on_verify=not args.no_verify
    )
    sys.stdout.write(json.dumps(dump_composition_catalog(catalog), indent=2, sort_keys=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
