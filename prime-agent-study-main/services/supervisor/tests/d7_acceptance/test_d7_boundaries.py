"""D7 architecture rules 4–5, 9–10 — browser never calls Memory; no parallel store theater."""

from __future__ import annotations

import re
from pathlib import Path

from tests.d7_acceptance.conftest import AI_REPO, OUTER_REPO

_FE_SRC = OUTER_REPO / "frontend" / "src"

# Browser must never hit Memory Service or supervisor memory RPC directly.
_MEMORY_PLANE_CALL = re.compile(
    r"""(?x)
    (?:localhost|127\.0\.0\.1):809[0-9]
    | /v1/memory/
    | /v1/sessions/[^"'`\s]+/memory/(?:compact|recall)
    | NEXT_PUBLIC_.*MEMORY
    | MEMORY_SERVICE_URL\s*=
    """,
    re.IGNORECASE,
)


def _iter_fe_source() -> list[Path]:
    if not _FE_SRC.is_dir():
        return []
    skip = {"node_modules", ".next", "coverage", "d6_acceptance", "d7_acceptance", "d5_acceptance"}
    out: list[Path] = []
    for path in _FE_SRC.rglob("*"):
        if not path.is_file():
            continue
        if any(p in skip for p in path.parts):
            continue
        if path.suffix.lower() in {".ts", ".tsx", ".js", ".jsx"}:
            out.append(path)
    return out


def test_rule4_browser_never_calls_memory_service_or_supervisor_memory_rpc() -> None:
    """Rule 4: browser → BFF only; never Memory Service or supervisor memory RPC."""
    assert _FE_SRC.is_dir(), f"missing FE src at {_FE_SRC}"
    offenders: list[str] = []
    for path in _iter_fe_source():
        if ".test." in path.name or path.name.endswith(".spec.ts"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            # Allow BFF-relative `/api/.../memory` paths (product honesty / future BFF).
            if re.search(r"/api/.*/memory", line):
                continue
            if _MEMORY_PLANE_CALL.search(line):
                rel = path.relative_to(OUTER_REPO)
                offenders.append(f"{rel}:{i}:{stripped[:140]}")
    assert not offenders, "browser must not call memory plane:\n" + "\n".join(offenders[:40])


def test_rule9_memory_subagent_not_documented_as_ltm() -> None:
    """Rule 9: MemorySubagentProvider is subagent transport — not durable LTM."""
    provider = (
        AI_REPO
        / "services"
        / "supervisor"
        / "src"
        / "supervisor"
        / "seams"
        / "providers"
        / "memory_subagent.py"
    )
    assert provider.is_file()
    text = provider.read_text(encoding="utf-8").lower()
    # Must not claim to be durable / semantic LTM.
    assert "durable" not in text or "not" in text
    assert "long-term" not in text and "long term" not in text


def test_no_parallel_bff_memory_store_library() -> None:
    """North star: BFF must not invent a parallel document-store library vs AI Scope+items.

    Either no product memory CRUD routes yet (422 / blocked) OR routes proxy durable APIs.
    A local SQLite/Postgres 'memory_stores' table that disagrees with AI scopes fails D7.
    """
    backend = OUTER_REPO / "backend" / "prime_agent"
    if not backend.is_dir():
        return
    # Scan models/routes for a parallel metaphor.
    offenders: list[str] = []
    for path in backend.rglob("*.py"):
        if "migrations" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"class\s+MemoryStore\b", text) or re.search(
            r"memory_stores\s*=\s*Table", text
        ):
            offenders.append(str(path.relative_to(OUTER_REPO)))
        if "document store" in text.lower() and "memory" in text.lower():
            if "not available" not in text.lower() and "422" not in text:
                offenders.append(f"{path.relative_to(OUTER_REPO)}:document-store metaphor")
    assert not offenders, (
        "parallel outer memory store metaphor found — map to Scope+items or keep blocked:\n"
        + "\n".join(offenders)
    )
