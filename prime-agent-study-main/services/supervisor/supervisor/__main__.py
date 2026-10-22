"""One image, two roles: ``python -m supervisor api`` or ``python -m supervisor worker``.

- ``api``: accepts requests and starts or signals each session's Temporal workflow; never runs
  an agent. ``SERVE_SUPERVISOR_API``, ``SERVE_MODEL_GATEWAY`` and ``SERVE_MEMORY_SERVICE`` pick
  what it serves (the gateway and memory service run from this same image).
- ``worker``: runs the session workflows (model calls, tools, sandboxes). Run more of them on
  the same queue for more load; ``WORKER_MAX_CONCURRENT_RUNS`` caps each one.

This is the production split of LangGraph's API servers and queue workers: scale each on its
own, and always keep at least one worker, or work waits (``GET /v1/ops/workers`` says so).
"""

from __future__ import annotations

import sys

ROLES = ("api", "worker")


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    role = args[0] if args else "api"
    if role == "api":
        from supervisor.main import main as serve

        serve()
    elif role == "worker":
        from supervisor.temporal.worker import main as work

        work()
    else:
        raise SystemExit(f"unknown role {role!r}: use one of {', '.join(ROLES)}")


if __name__ == "__main__":
    main()
