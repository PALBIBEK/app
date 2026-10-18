"""WP-M7 mass-assignment — model cannot inject control-plane fields into tools."""

from __future__ import annotations

from pathlib import Path

from supervisor.agent.runtimes.githi.tools.provider import GithiToolProvider
from supervisor.sandbox.workspace_tree import LocalFsTreeIO
from supervisor.seams.types import ToolInvokeContext


def test_provider_strips_control_plane_args(tmp_path: Path) -> None:
    tree = LocalFsTreeIO(tmp_path)
    provider = GithiToolProvider(
        "sess-bound",
        working_directory=str(tmp_path),
        sandbox_enabled=True,
        tree=tree,
    )
    result = provider.invoke(
        ToolInvokeContext(),
        "write",
        {
            "file_path": "safe.txt",
            "content": "ok",
            "scope_id": "attacker-scope",
            "principal_id": "evil",
            "tenant_id": "evil-tenant",
            "working_directory": "/tmp/hack",
            "session_id": "other-session",
            "grant": "scope:admin",
        },
    )
    assert result.ok is True
    assert (tmp_path / "safe.txt").read_text() == "ok"
    # Session identity on provider unchanged
    assert provider.session_id == "sess-bound"
    assert provider.working_directory == str(tmp_path)
