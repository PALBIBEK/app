"""``python -m supervisor rollout``: see and steer a deploy (supervisor.temporal.rollout).

    status                  the deployment's versions: current, ramping, draining, drained
    ramp BUILD PERCENT      send PERCENT of new workflows to BUILD (0 stops the ramp)
    promote BUILD           make BUILD current
    check [--limit N]       replay running and recent workflows against this code (the gate)
    check --dir DIR         replay saved histories instead
    export DIR [--limit N]  save histories for the CI replay gate
    policies                each workflow step's time limit, retries and worker-death policy

The deployment is ``WORKER_DEPLOYMENT_NAME`` (or ``--deployment``); Temporal is the usual
``TEMPORAL_ADDRESS`` / ``TEMPORAL_NAMESPACE``. Exits 1 when the gate fails or the request
is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from supervisor.config import TemporalSettings


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m supervisor rollout", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--deployment", default=None, help="worker deployment (default WORKER_DEPLOYMENT_NAME)")
    sub = p.add_subparsers(dest="command", required=True)
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    r = sub.add_parser("ramp")
    r.add_argument("build")
    r.add_argument("percent", type=float)
    pr = sub.add_parser("promote")
    pr.add_argument("build")
    c = sub.add_parser("check")
    c.add_argument("--limit", type=int, default=50)
    c.add_argument("--dir", type=Path, default=None)
    e = sub.add_parser("export")
    e.add_argument("dir", type=Path)
    e.add_argument("--limit", type=int, default=50)
    sub.add_parser("policies")
    return p


async def _run(args: argparse.Namespace) -> int:
    from supervisor.temporal import rollout

    if args.command == "policies":
        from supervisor.temporal.policies import policy_table

        print(policy_table())
        return 0
    if args.command == "check" and args.dir is not None:
        report = await rollout.replay(rollout.file_histories(args.dir))
        return _report(report)

    from supervisor.temporal.client import connect

    settings = TemporalSettings.from_env()
    client = await connect(settings)
    if args.command == "check":
        return _report(await rollout.check(client, limit=args.limit))
    if args.command == "export":
        print(f"saved {await rollout.export(client, args.dir, limit=args.limit)} histories to {args.dir}")
        return 0
    deployment = args.deployment or settings.deployment_name
    if not deployment:
        print("no deployment: set WORKER_DEPLOYMENT_NAME or pass --deployment", file=sys.stderr)
        return 2
    if args.command == "promote":
        await rollout.promote(client, deployment, args.build)
        print(f"{deployment}: {args.build} is current")
        return 0
    if args.command == "ramp":
        await rollout.ramp(client, deployment, args.build, args.percent)
        print(f"{deployment}: {args.build} gets {args.percent:g}% of new workflows")
        return 0
    found = await rollout.status(client, deployment)
    if found is None:
        print(f"{deployment}: no worker of this deployment has polled yet", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(found), indent=2))
        return 0
    ramping = f", ramping {found.ramping_build} at {found.ramping_percentage:g}%" if found.ramping_build else ""
    print(f"{deployment}: current {found.current_build or '(none)'}{ramping}")
    for v in found.versions:
        retire = "  safe to stop its workers" if v.safe_to_retire else ""
        print(f"  {v.build_id:<24} {v.status:<9} {v.drainage:<9}{retire}")
    return 0


def _report(report) -> int:  # type: ignore[no-untyped-def]
    for f in report.failures:
        print(f"NOT REPLAYABLE {f.workflow_id}: {f.error}", file=sys.stderr)
    print(f"replayed {report.checked} histories: {'ok' if report.ok else f'{len(report.failures)} failed'}")
    return 0 if report.ok else 1


def main(argv: list[str]) -> int:
    from temporalio.service import RPCError

    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except RPCError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
