"""Under workspace_write, commands run where they are confined (F15).

A shell inside a container can only change the container, as the file tools
can; on a host-process sandbox it could change the host. Before, shells were
refused under workspace_write everywhere, so the shipped githi agent (Docker
sandbox, workspace_write) could never run bash.
"""

from __future__ import annotations

import pytest

from supervisor.policy.sandbox_policy import SandboxPolicy, SandboxPolicyConfig
from supervisor.policy.types import FileEffects


@pytest.mark.parametrize(("isolated", "allowed"), [(True, True), (False, False)])
def test_a_shell_runs_under_workspace_write_only_in_an_isolating_sandbox(isolated: bool, allowed: bool) -> None:
    policy = SandboxPolicy()
    policy.configure("s", SandboxPolicyConfig(file_effects=FileEffects.WORKSPACE_WRITE), isolated=isolated)
    denial = policy.check_tool_invoke("s", "bash", tool_class="non_atomic")
    assert (denial is None) is allowed


def test_read_only_still_refuses_a_shell_in_any_sandbox() -> None:
    policy = SandboxPolicy()
    policy.configure("s", SandboxPolicyConfig(file_effects=FileEffects.READ_ONLY), isolated=True)
    assert policy.check_tool_invoke("s", "bash", tool_class="non_atomic") is not None
