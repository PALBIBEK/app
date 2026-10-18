"""Architecture leak checks — failing tests, not lint warnings."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "supervisor"
PI_KEYS = re.compile(
    r"\b(toolCallId|isError|stopReason|cacheRead|cacheWrite|"
    r"isStreaming|isCompacting|pendingMessageCount|sessionId|"
    r"messageCount|contextWindow|toolName)\b"
)
MEMBRANE = {
    SRC / "agent" / "runtimes" / "harness" / "translate.py",
    SRC / "agent" / "runtimes" / "harness" / "commands.py",
    SRC / "agent" / "runtimes" / "harness" / "state.py",
    SRC / "seams" / "providers" / "mcp_tools.py",
}
RUNTIMES_ROOT = SRC / "agent" / "runtimes"


def test_no_camelcase_harness_wire_keys_outside_membrane() -> None:
    leaks: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in MEMBRANE:
            continue
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            for match in PI_KEYS.finditer(line):
                leaks.append(f"{path.relative_to(SRC)}:{i}:{match.group(0)}")
    assert leaks == [], "camelCase harness wire keys leaked:\n" + "\n".join(leaks)


def test_nothing_outside_runtimes_imports_runtimes() -> None:
    """session/, journal/, api/, … must not know a concrete runtime exists.

    ``agent/factory.py`` is the intentional single choke-point.
    """
    allow = {SRC / "agent" / "factory.py"}
    leaks: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in allow:
            continue
        try:
            path.relative_to(RUNTIMES_ROOT)
            continue  # inside runtimes/ — allowed
        except ValueError:
            pass
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "runtimes" in line:
                leaks.append(f"{path.relative_to(SRC)}:{i}:{line.strip()}")
    assert leaks == [], "runtimes referenced outside agent/runtimes/:\n" + "\n".join(leaks)


def test_echo_does_not_depend_on_harness() -> None:
    echo = RUNTIMES_ROOT / "echo"
    leaks: list[str] = []
    for path in echo.rglob("*.py"):
        text = path.read_text()
        if "runtimes.harness" in text:
            leaks.append(str(path.relative_to(SRC)))
    assert leaks == [], "echo imports harness:\n" + "\n".join(leaks)


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
