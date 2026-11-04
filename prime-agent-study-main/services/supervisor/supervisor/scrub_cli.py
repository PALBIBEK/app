"""``python -m supervisor scrub``: run ``supervisor.journal.scrub`` against the deployment's
database and blob store (the wiring the journal package itself must not import)."""

from __future__ import annotations

import argparse
import asyncio
import logging

from supervisor.journal.scrub import ScrubReport, scrub

_BATCH = 500


def _position(text: str) -> tuple[str, int]:
    session_id, _, seq = text.rpartition(":")
    if not session_id:
        raise argparse.ArgumentTypeError("expected <session_id>:<seq>")
    return session_id, int(seq)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m supervisor scrub", description="Mask the credentials journalled before the journal masked them.")
    parser.add_argument("--apply", action="store_true", help="rewrite (without it: count what would change)")
    parser.add_argument("--after", type=_position, help="carry on after <session_id>:<seq>")
    parser.add_argument("--batch", type=int, default=_BATCH)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return asyncio.run(_run(apply=args.apply, after=args.after, batch=args.batch))


async def _run(*, apply: bool, after: tuple[str, int] | None, batch: int) -> int:
    from supervisor.bootstrap import journal_blobs
    from supervisor.config import Settings
    from supervisor.db.engine import make_engine, session_factory
    from supervisor.seams.providers.workspace.workspace_store_factory import (
        create_object_storage_for_workspace,
    )

    settings = Settings()
    engine = make_engine(settings.database_url)
    blobs = journal_blobs(create_object_storage_for_workspace(), settings.blob_dir)

    def said(r: ScrubReport) -> None:
        where = f"{r.position[0]}:{r.position[1]}" if r.position else "-"
        print(f"read {r.events} events, {r.events_masked} to mask, {r.blobs_masked} blobs; at {where}", flush=True)

    try:
        report = await scrub(session_factory(engine), blobs, apply=apply, after=after, batch=batch, progress=said)
    finally:
        await engine.dispose()
    verb = "masked" if apply else "would mask"
    print(
        f"done: {verb} {report.events_masked} of {report.events} events, {report.blobs_masked} blobs, "
        f"{report.outbox_masked} tool outbox rows, {report.titles_masked} titles; "
        f"deleted {report.blobs_deleted} replaced blobs",

    )
    if not apply:
        print("dry run: nothing was changed; run again with --apply (after a backup) to rewrite")
    elif report.events_masked or report.outbox_masked or report.titles_masked:
        print("now VACUUM events, tool_outbox and session_index (old row versions stay until then), "
              "and rotate the credentials that were journalled")
    return 0
