"""Bootstrap composition catalog at process start (DeepSeek boot() parity)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from supervisor.catalog.core.types import AgentComposition
from supervisor.catalog.loading.loader import load_composition_catalog
from supervisor.catalog.loading.verify import verify_composition_catalog
from supervisor.catalog.storage import CompositionCatalog

logger = logging.getLogger(__name__)

# Problems look like ``githi-tools-v1.json: …`` or ``githi-v1: …``.
_PROBLEM_COMP_RE = re.compile(r"^([^:\s]+?)(?:\.json)?:\s*(.*)$")


def agent_platform_root() -> Path:
    """Repo root of the agent platform (the directory holding ``contracts/`` and ``harness/``).

    Found by walking up from this file rather than counting levels, so moving
    modules around cannot silently point it at the wrong directory. Falls back
    to the layout ``<root>/services/supervisor/src/supervisor/...``.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "contracts").is_dir() and (parent / "harness").is_dir():
            return parent
    for parent in here.parents:
        if parent.name == "services" and (parent / "supervisor").is_dir():
            return parent.parent
    return here.parents[-1]


def default_catalog_dir() -> Path:
    return agent_platform_root() / "harness"


def resolve_catalog_dir(explicit: Path | str | None = None) -> Path:
    """Two-anchor resolution: explicit arg → ``YAL_CATALOG_DIR`` → shipped harness tree."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("YAL_CATALOG_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return default_catalog_dir()


def _composition_names_from_problems(problems: list[str]) -> dict[str, list[str]]:
    """Group verify messages by composition name (best-effort)."""
    by_name: dict[str, list[str]] = {}
    for problem in problems:
        match = _PROBLEM_COMP_RE.match(problem)
        if match is None:
            by_name.setdefault("_catalog", []).append(problem)
            continue
        by_name.setdefault(match.group(1), []).append(match.group(2) or problem)
    return by_name


def soft_skip_invalid_compositions(
    catalog: CompositionCatalog,
    problems: list[str],
) -> list[str]:
    """Deprecate offered compositions implicated by verify problems; return skipped names."""
    skipped: list[str] = []
    for name, reasons in _composition_names_from_problems(problems).items():
        if name == "_catalog":
            for reason in reasons:
                logger.warning("composition catalog verify (non-composition): %s", reason)
            continue
        offered = [
            art
            for art in catalog.list_artifacts("composition", status="offered")
            if art.name == name and isinstance(art, AgentComposition)
        ]
        if not offered:
            continue
        for art in offered:
            catalog.deprecate("composition", art.name, art.digest)
        skipped.append(name)
        logger.warning(
            "composition catalog soft-skip composition=%s reasons=%s",
            name,
            "; ".join(reasons),
        )
    return skipped


def bootstrap_composition_catalog(
    catalog_dir: Path | str | None = None,
    *,
    fail_on_verify: bool = True,
) -> CompositionCatalog:
    """Load shipped artifacts; soft-skip invalid compositions when verify is enforced.

    Fail-closed only when catalog is required (``fail_on_verify=True``) and **zero**
    offered compositions remain after soft-skip. Incomplete WIP fixtures must not
    brick supervisor boot when valid compositions (e.g. coding-v1) are present.
    """
    root = resolve_catalog_dir(catalog_dir)
    catalog = load_composition_catalog(root)
    problems = verify_composition_catalog(catalog, root)
    if not problems:
        return catalog

    if not fail_on_verify:
        return catalog

    soft_skip_invalid_compositions(catalog, problems)
    offered = catalog.list_artifacts("composition", status="offered")
    if not offered:
        raise RuntimeError(
            "composition catalog verify failed: zero offered compositions after soft-skip:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )

    remaining = verify_composition_catalog(catalog, root)
    if remaining:
        # Disk fixtures may still be marked offered while in-memory rows were
        # deprecated — log and continue as long as at least one offered remains.
        logger.warning(
            "composition catalog soft-skip left %d verify note(s); offered=%s",
            len(remaining),
            sorted({a.name for a in offered}),
        )
    return catalog
