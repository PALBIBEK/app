from __future__ import annotations

import shlex
from typing import Any, Final

from supervisor.tools.args import ToolArgs
from supervisor.tools.context import ToolContext
from supervisor.tools.errors import InvalidArguments
from supervisor.tools.registry import tool
from supervisor.tools.spec import ToolClass

_NOT_A_REPO: Final = 3

# Commit as the agent unless the repository names an author itself.
_SCRIPT: Final = """\
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit {not_a_repo}
git config user.email >/dev/null || set -- -c user.name='YAL agent' -c user.email=agent@yal.invalid
git add -A && git "$@" commit --allow-empty -m {message}
"""


class GitCommitArgs(ToolArgs):
    message: str
    """Commit message"""


@tool(tool_class=ToolClass.TRANSACTIONAL)
async def git_commit(args: GitCommitArgs, ctx: ToolContext) -> dict[str, Any]:
    """Stage every change in the workspace and create a git commit."""
    if not args.message.strip():
        raise InvalidArguments("message must not be empty")
    script = _SCRIPT.format(not_a_repo=_NOT_A_REPO, message=shlex.quote(args.message))
    result = await ctx.workspace.run(script, timeout_s=max(ctx.timeout_s - 2.0, 1.0))
    if result.exit_code == _NOT_A_REPO:
        return {"committed": False, "reason": "the workspace is not a git repository"}
    ctx.mutated()
    return {
        "committed": result.exit_code == 0,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
