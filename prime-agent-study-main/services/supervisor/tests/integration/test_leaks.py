"""Architecture leak checks — failing tests, not lint warnings."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "supervisor"
PI_KEYS = re.compile(
    r"\b(toolCallId|isError|stopReason|cacheRead|cacheWrite|"
    r"isStreaming|isCompacting|pendingMessageCount|sessionId|"
    r"messageCount|contextWindow|toolName)\b"
)
MEMBRANE = {
    SRC / "seams" / "providers" / "mcp_tools.py",
}
RUNTIMES_ROOT = SRC / "agent" / "runtimes"



def test_journal_does_not_import_runtimes() -> None:
    journal = SRC / "journal"
    leaks: list[str] = []
    for path in journal.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                leaks.append(f"{path.relative_to(SRC)}:{i}:{line.strip()}")
    assert leaks == [], "journal imports runtimes:\n" + "\n".join(leaks)


def test_api_does_not_import_runtimes() -> None:
    api = SRC / "api"
    leaks: list[str] = []
    for path in api.rglob("*.py"):
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                leaks.append(f"{path.relative_to(SRC)}:{i}:{line.strip()}")
    assert leaks == [], "api imports runtimes:\n" + "\n".join(leaks)


def test_api_journal_only_in_events_deps_wire() -> None:
    """sessions/health must not touch journal; events may for replay."""
    allow = {"events.py", "deps.py", "wire.py"}
    api = SRC / "api"
    leaks: list[str] = []
    for path in api.rglob("*.py"):
        if path.name in allow:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "journal" in line:
                leaks.append(f"{path.relative_to(SRC)}:{i}:{line.strip()}")
    assert leaks == [], "journal leaked outside events/deps/wire:\n" + "\n".join(leaks)
