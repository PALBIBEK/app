"""D5 T12 / §6 A — gateway package isolation (no supervisor imports)."""

from __future__ import annotations

import ast
from pathlib import Path


def _gateway_src_root() -> Path:
    # tests/d5_acceptance/ → tests/ → model-gateway/ → src/model_gateway
    return Path(__file__).resolve().parents[2] / "src" / "model_gateway"


def test_t12_no_supervisor_imports_in_gateway_package() -> None:
    """T12 / architecture rule 6: model_gateway MUST NOT import supervisor.*."""
    root = _gateway_src_root()
    assert root.is_dir(), f"missing gateway package at {root}"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "supervisor" or alias.name.startswith("supervisor."):
                        offenders.append(f"{path}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "supervisor" or mod.startswith("supervisor."):
                    offenders.append(f"{path}:{node.lineno}: from {mod}")
    assert not offenders, "supervisor imports forbidden in gateway:\n" + "\n".join(offenders)


def test_t12_gateway_package_importable_alone() -> None:
    """T12: `import model_gateway` works without supervisor on sys.path."""
    import model_gateway
    import model_gateway.app
    import model_gateway.config
    import model_gateway.virtual_keys  # noqa: F401
