"""The browser code mode benchmark: a real Django admin and a hard single-page shop,
driven through the supervisor's own ``browser_exec`` by whoever plays the model.

    python run.py serve              # fresh data (migrate, seed, hidden answers), server on :8800
    python run.py call A D1 <<'PY'   # one tool call as the model makes it; prints its result
    ... code ...
    PY
    python run.py score              # the site's state against the hidden answers
    python run.py summary            # calls, failures, result size and re-read tokens per arm

``serve`` and ``score`` need Django 5.2; ``call`` runs in the supervisor's own environment
(its virtualenv's python) and needs the sandbox runtime on this machine (python3 with
Playwright and Chromium), as the tool's tests do. Each arm
gets its own workspace and browser (``work/ws-<arm>``); every call is logged to
``results/calls.jsonl``.
"""

from __future__ import annotations

import asyncio
import collections
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE / "site"
WORK = HERE / "work"
LOG = HERE / "results" / "calls.jsonl"
SUPERVISOR = HERE.parents[2] / "services" / "supervisor"
PORT = 8800


def serve() -> None:
    """Fresh data and a server: an arm that changes the site starts from the same state."""
    pid = WORK / "server.pid"
    if pid.exists():
        subprocess.run(["kill", pid.read_text().strip()], check=False)
        time.sleep(1)
    (SITE / "db.sqlite3").unlink(missing_ok=True)
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "settings", "PYTHONPATH": str(SITE)}
    subprocess.run([sys.executable, "-m", "django", "migrate", "-v", "0"], cwd=SITE, env=env, check=True)
    subprocess.run([sys.executable, str(HERE / "seed.py")], check=True)
    subprocess.run([sys.executable, str(HERE / "oracle.py"), "before"], check=True)
    WORK.mkdir(exist_ok=True)
    server = subprocess.Popen(
        [sys.executable, "-m", "django", "runserver", f"127.0.0.1:{PORT}", "--noreload", "--insecure"],
        cwd=SITE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    pid.write_text(str(server.pid))
    print(f"serving http://127.0.0.1:{PORT}/admin/ (librarian / Shelf-2026!) and /shop")


async def call(arm: str, task: str, session: str | None) -> None:
    """One ``browser_exec`` call, exactly as the agent's model makes it."""
    sys.path.insert(0, str(SUPERVISOR))
    import supervisor.tools.builtin  # noqa: F401 — registers the tools
    from supervisor.tools import default_registry
    from supervisor.tools.context import ToolContext
    from supervisor.tools.workspace import HostWorkspace

    workspace = WORK / f"ws-{arm}"
    workspace.mkdir(parents=True, exist_ok=True)
    env = {"GITHI_BROWSER_HOME": str(WORK / f"browser-{arm}")}
    if browsers := os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = browsers
    code = sys.stdin.read()
    ctx = ToolContext(bound_workspace=HostWorkspace(workspace), timeout_s=60, env=env)
    started = time.monotonic()
    try:
        result = dict(await default_registry()["browser_exec"].run({"code": code, **({"session": session} if session else {})}, ctx))
    except Exception as exc:  # noqa: BLE001 — a refused call is a result too
        result = {"tool_error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(result, ensure_ascii=False)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as log:
        log.write(json.dumps({"arm": arm, "task": task, "code_bytes": len(code), "result_chars": len(text),
                              "seconds": round(time.monotonic() - started, 2), "exit": result.get("exit_code"),
                              "error": (result.get("error") or "").strip().splitlines()[-1:]}) + "\n")
    print(text)


def summary() -> None:
    """Per arm and task: calls, failed calls, result characters, and the input tokens a model
    re-reads (each turn resends every earlier result, so per-action costs compound)."""
    rows = [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]
    rows = [r for r in rows if not r.get("excluded")]
    seen: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        seen[(r["arm"], r["task"])].append(r)
    print(f"{'arm':4}{'task':6}{'calls':>6}{'failed':>8}{'result chars':>14}{'re-read tokens':>16}{'seconds':>9}")
    for (arm, task), calls in sorted(seen.items()):
        running = reread = 0
        for r in calls:
            running += r["result_chars"] + r["code_bytes"]
            reread += running
        tool_calls = [r for r in calls if not r.get("note")]
        failed = sum(1 for r in tool_calls if r["exit"] not in (0, None))
        chars = sum(r["result_chars"] for r in calls)
        print(f"{arm:4}{task:6}{len(tool_calls):>6}{failed:>8}{chars:>14,}{reread // 4:>16,}{sum(r['seconds'] for r in calls):>9.1f}")


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["serve"]:
            serve()
        case ["call", arm, task, *session]:
            asyncio.run(call(arm, task, session[0] if session else None))
        case ["score"]:
            subprocess.run([sys.executable, str(HERE / "oracle.py"), "after"], check=True)
        case ["summary"]:
            summary()
        case ["clean"]:
            shutil.rmtree(WORK, ignore_errors=True)
        case _:
            sys.exit(__doc__)
