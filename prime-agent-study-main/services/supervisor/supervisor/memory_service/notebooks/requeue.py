"""Queue again the automatic captures set aside (G2's inbox), once the fault that set them
aside is fixed: an operator's step.

A capture that keeps failing to land on its own is set aside after ``PROMOTE_ATTEMPTS``
and kept ``KEEP_SET_ASIDE_MS`` (the ``inbox_set_aside`` metric counts them). Against the
replicas' database (``MEMORY_DATABASE_URL``)::

    python -m supervisor.memory_service.notebooks.requeue              # every notebook
    python -m supervisor.memory_service.notebooks.requeue --notebook ID

The drainers land what this queues on their next pass.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping, Sequence

from supervisor.memory_service.db import database_url_from_env, make_engine, make_session_factory
from supervisor.memory_service.notebooks.ops import PROMOTE_ATTEMPTS
from supervisor.memory_service.notebooks.store import NotebookStore

__all__ = ["main", "requeue"]


async def requeue(database_url: str, notebook_id: str | None = None) -> int:
    """Queue the set-aside captures again (in ``notebook_id``, or every notebook); how many."""
    engine = make_engine(database_url)
    try:
        store = NotebookStore(make_session_factory(engine))
        return await store.requeue_set_aside(notebook_id, give_up_at=PROMOTE_ATTEMPTS)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--notebook", help="only this notebook's set-aside captures")
    args = parser.parse_args(argv)
    count = asyncio.run(requeue(database_url_from_env(environ), args.notebook))
    print(f"{count} set-aside captures queued again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
