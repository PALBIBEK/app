"""D6 §5 F / architecture rule 3 — no browser → supervisor."""

from __future__ import annotations

import re
from pathlib import Path

from tests.d6_acceptance.conftest import OUTER_REPO

_FE_SRC = OUTER_REPO / "frontend" / "src"

# Direct supervisor / AI-plane hosts or paths from browser bundles.
_SUPERVISOR_CALL = re.compile(
    r"""(?x)
    (?:localhost|127\.0\.0\.1):8080
    | /v1/sessions/[^"'`\s]+/tools/invoke
    | /v1/catalog/
    | NEXT_PUBLIC_.*SUPERVISOR
    | SUPERVISOR_BASE_URL\s*=
    """,
    re.IGNORECASE,
)


def _iter_fe_source() -> list[Path]:
    if not _FE_SRC.is_dir():
        return []
    skip = {"node_modules", ".next", "coverage", "d6_acceptance"}
    out: list[Path] = []
    for path in _FE_SRC.rglob("*"):
        if not path.is_file():
            continue
        if any(p in skip for p in path.parts):
            continue
        if path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}:
            out.append(path)
    return out


def test_f_no_browser_supervisor_invoke_or_catalog() -> None:
    """§5 F / rule 3: FE must not call supervisor tools/invoke or catalog directly."""
    assert _FE_SRC.is_dir(), f"missing FE src at {_FE_SRC}"
    offenders: list[str] = []
    for path in _iter_fe_source():
        # Test doubles may mention supervisor strings; skip *.test.*
        if ".test." in path.name or path.name.endswith(".spec.ts"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("//") or line.strip().startswith("*"):
                continue
            if _SUPERVISOR_CALL.search(line):
                rel = path.relative_to(OUTER_REPO)
                offenders.append(f"{rel}:{i}:{line.strip()[:140]}")
    assert not offenders, "browser must not call supervisor:\n" + "\n".join(offenders[:40])
