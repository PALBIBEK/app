"""Built-in in-process tool implementations for catalog ToolSets."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from supervisor.agent.runtimes.githi.tools.paths import PathEscapeError, resolve_in_workspace
from supervisor.seams import ToolInvokeContext
from supervisor.session import session_workspace

# Handlers may take ``(args)`` or ``(args, ctx)``.
BuiltinHandler = Callable[..., Any]

# Custom / HTTP-registered handlers (checked before builtins).
_CUSTOM_HANDLERS: dict[str, BuiltinHandler] = {}


def register_handler(name: str, handler: BuiltinHandler) -> None:
    """Register an in-process handler for a catalog / custom tool name."""
    if not name or not callable(handler):
        raise ValueError("register_handler requires a non-empty name and callable")
    _CUSTOM_HANDLERS[name] = handler


def _call_handler(
    handler: BuiltinHandler,
    args: dict[str, Any],
    ctx: ToolInvokeContext | None,
) -> Any:
    try:
        return handler(args, ctx)
    except TypeError:
        return handler(args)


def _workspace_root(ctx: ToolInvokeContext | None) -> Path:
    if ctx is not None:
        sid = (ctx.extra or {}).get("session_id")
        if sid:
            return session_workspace(str(sid))
    return Path.cwd()


def _path_arg(args: dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = args.get(key)
        if val is not None and str(val) != "":
            return str(val)
    return ""


def _echo(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> dict[str, str]:
    del ctx
    return {"text": str(args.get("text", ""))}


def _read(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> Any:
    rel = _path_arg(args, "file_path", "path")
    if not rel:
        raise ValueError("read requires file_path or path")
    root = _workspace_root(ctx)
    resolved = resolve_in_workspace(root, rel)
    if not resolved.is_file():
        raise FileNotFoundError(f"File not found: {rel}")
    text = resolved.read_text(encoding="utf-8", errors="replace")
    offset = args.get("offset")
    limit = args.get("limit")
    if offset is None and limit is None:
        return text
    lines = text.splitlines(keepends=True)
    start_idx = (int(offset) - 1) if offset else 0
    end_idx = (start_idx + int(limit)) if limit else len(lines)
    return "".join(lines[start_idx:end_idx])


def _write(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> dict[str, str]:
    rel = _path_arg(args, "file_path", "path")
    if not rel:
        raise ValueError("write requires file_path or path")
    content = str(args.get("content", ""))
    root = _workspace_root(ctx)
    resolved = resolve_in_workspace(root, rel)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return {"path": rel, "bytes": str(len(content.encode("utf-8")))}


def _ls(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> Any:
    rel = _path_arg(args, "path") or "."
    root = _workspace_root(ctx)
    resolved = resolve_in_workspace(root, rel)
    if not resolved.exists():
        raise FileNotFoundError(f"path not found: {rel}")
    show_hidden = bool(args.get("show_hidden", False))
    if resolved.is_file():
        return [resolved.name]
    names: list[str] = []
    for entry in sorted(resolved.iterdir(), key=lambda p: p.name):
        if not show_hidden and entry.name.startswith("."):
            continue
        names.append(entry.name + ("/" if entry.is_dir() else ""))
    return names


def _grep(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> Any:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        raise ValueError("grep requires pattern")
    root = _workspace_root(ctx)
    path_arg = _path_arg(args, "path") or "."
    start = resolve_in_workspace(root, path_arg)
    flags = re.IGNORECASE if args.get("case_insensitive") else 0
    rx = re.compile(pattern, flags)
    max_matches = int(args.get("max_matches") or 100)
    glob_pat = args.get("glob")
    matches: list[str] = []
    files: list[Path]
    if start.is_file():
        files = [start]
    else:
        files = sorted(p for p in start.rglob("*") if p.is_file())
    for path in files:
        if glob_pat and not path.match(str(glob_pat)):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                rel = path.relative_to(root) if path.is_relative_to(root) else path
                matches.append(f"{rel}:{i}:{line}")
                if len(matches) >= max_matches:
                    return matches
    return matches


def _bash(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> dict[str, Any]:
    command = str(args.get("command", ""))
    if not command:
        raise ValueError("bash requires command")
    cwd = _workspace_root(ctx)
    completed = subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=float(args.get("timeout") or 30),
        env={**os.environ, "PWD": str(cwd)},
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _git_commit(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> dict[str, Any]:
    message = str(args.get("message", ""))
    if not message:
        raise ValueError("git_commit requires message")
    cwd = _workspace_root(ctx)
    # Best-effort: stage + commit when .git exists; otherwise acknowledge intent.
    if not (cwd / ".git").exists():
        return {"ok": True, "committed": False, "message": message, "reason": "no_git_repo"}
    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    commit = subprocess.run(
        ["git", "commit", "-m", message, "--allow-empty"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": commit.returncode == 0,
        "committed": commit.returncode == 0,
        "message": message,
        "stdout": commit.stdout,
        "stderr": commit.stderr or add.stderr,
    }


def write_memory_item(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Re-export so tests can patch ``tool_handlers.write_memory_item``."""
    from supervisor.seams.services import write_memory_item as _write

    return _write(*args, **kwargs)


def _remember(args: dict[str, Any], ctx: ToolInvokeContext | None = None) -> dict[str, Any]:
    from supervisor.seams.services import resolve_memory_scope

    content = str(args.get("content", ""))
    if not content:
        raise ValueError("remember requires content")
    extra = (ctx.extra if ctx is not None else {}) or {}
    session_id = str(extra.get("session_id") or "")
    tenant_id = str(extra.get("tenant_id") or "")
    scope = resolve_memory_scope(session_id=session_id, tenant_id=tenant_id)
    return write_memory_item(
        content=content,
        scope=scope,
        source_session=session_id or None,
    )


BUILTIN_HANDLERS: dict[str, BuiltinHandler] = {
    "echo": _echo,
    "read": _read,
    "write": _write,
    "ls": _ls,
    "grep": _grep,
    "bash": _bash,
    "git_commit": _git_commit,
    "remember": _remember,
}


def get_handler(name: str) -> BuiltinHandler | None:
    return _CUSTOM_HANDLERS.get(name) or BUILTIN_HANDLERS.get(name)


def invoke_builtin(
    name: str,
    args: dict[str, Any],
    ctx: ToolInvokeContext | None = None,
) -> Any:
    """Run a built-in or registered handler; raises ``KeyError`` when unknown."""
    handler = get_handler(name)
    if handler is None:
        raise KeyError(name)
    try:
        return _call_handler(handler, args, ctx)
    except PathEscapeError:
        raise
