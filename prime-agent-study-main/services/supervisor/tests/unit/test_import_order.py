"""Each entry point imports on its own, first, in a fresh interpreter.

Found while scripting the catalog: ``import supervisor.tools`` first failed with a
circular import (seams.services -> sandbox -> providers -> seams.services). Code
running in the app never saw it only because something else was imported earlier.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

ENTRY_POINTS = (
    "supervisor.tools",
    "supervisor.sandbox",
    "supervisor.seams.services",
    "supervisor.memory",
    "supervisor.main",
    "supervisor.temporal.worker",
    "supervisor.gateway.app",
    "supervisor.memory_service.app",
)


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_imports_first_in_a_fresh_interpreter(module: str) -> None:
    code = f"import {module}; from supervisor.tools import default_registry; default_registry()"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
