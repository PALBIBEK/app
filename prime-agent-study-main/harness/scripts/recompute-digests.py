#!/usr/bin/env python3
"""Recompute sha256 digests for harness catalog JSON artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "services" / "supervisor" / "src"))

from supervisor.catalog.bootstrap import bootstrap_composition_catalog
from supervisor.catalog.digests import content_digest
from supervisor.catalog.loader import _normalize_tool_set, _resolve_ref
from supervisor.catalog.store import prompt_pack_from_dict
from supervisor.catalog.types import AgentComposition, DigestRef, PlugBindings


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    catalog_dir = ROOT

    for path in sorted((catalog_dir / "tool_sets").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        ts = _normalize_tool_set(raw)
        body = ts.body_for_digest()
        raw["tools"] = body["tools"]
        raw["definitions"] = body["definitions"]
        _write_json(path, raw)
        print(f"tool_set {raw['name']}: {content_digest(body)}")

    for path in sorted((catalog_dir / "compositions").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if "tool_set" in raw:
            raw["tool_set"]["digest"] = "sha256:REPLACE_AFTER_OFFER"
        if "prompt_pack" in raw:
            raw["prompt_pack"]["digest"] = "sha256:REPLACE_AFTER_OFFER"
        raw["digest"] = "sha256:REPLACE_AFTER_OFFER"
        _write_json(path, raw)

    for path in sorted((catalog_dir / "prompt_packs").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        pack = prompt_pack_from_dict(raw)
        digest = content_digest(pack.body_for_digest())
        raw["digest"] = digest
        _write_json(path, raw)
        print(f"prompt_pack {raw['name']}: {digest}")

    catalog = bootstrap_composition_catalog(catalog_dir)
    for path in sorted((catalog_dir / "compositions").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        pp = _resolve_ref(catalog, "prompt_pack", raw["prompt_pack"]["id"], "")
        ts = _resolve_ref(catalog, "tool_set", raw["tool_set"]["id"], "")
        sp_ref_digest: DigestRef | None = None
        sp_raw = raw.get("sandbox_profile")
        if sp_raw is not None:
            sp = _resolve_ref(catalog, "sandbox_profile", sp_raw["id"], "")
            sp_ref_digest = DigestRef(id=sp.name, digest=sp.digest)
        mp_ref_digest: DigestRef | None = None
        mp_raw = raw.get("memory_profile")
        if mp_raw is not None:
            mp = _resolve_ref(catalog, "memory_profile", mp_raw["id"], "")
            mp_ref_digest = DigestRef(id=mp.name, digest=mp.digest)
        comp = AgentComposition(
            name=raw["name"],
            semver=raw["semver"],
            digest="",
            protocol_version=int(raw.get("protocol_version", 1)),
            status=raw.get("status", "offered"),
            harness_version=raw["harness_version"],
            prompt_pack=DigestRef(id=pp.name, digest=pp.digest),
            tool_set=DigestRef(id=ts.name, digest=ts.digest),
            sandbox_profile=sp_ref_digest,
            memory_profile=mp_ref_digest,
            plug_bindings=PlugBindings.from_dict(raw.get("plug_bindings")),
        )
        digest = content_digest(comp.body_for_digest())
        raw["digest"] = digest
        raw["prompt_pack"] = {"id": pp.name, "digest": pp.digest}
        raw["tool_set"] = {"id": ts.name, "digest": ts.digest}
        if sp_ref_digest is not None:
            raw["sandbox_profile"] = {
                "id": sp_ref_digest.id,
                "digest": sp_ref_digest.digest,
            }
        if mp_ref_digest is not None:
            raw["memory_profile"] = {
                "id": mp_ref_digest.id,
                "digest": mp_ref_digest.digest,
            }
        _write_json(path, raw)
        print(f"composition {raw['name']}: {digest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
