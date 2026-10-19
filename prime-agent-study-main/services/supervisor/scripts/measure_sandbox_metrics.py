#!/usr/bin/env python3
"""Record Phase 1 sandbox measurements for ARCHITECTURE Step 9.

Usage:
  python scripts/measure_sandbox_metrics.py [--image pi-harness:coding-v1]

Prints JSON lines suitable for pasting into the Step 9 metrics table.
Skips metrics when Docker is unavailable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time


def _have_docker() -> bool:
    return shutil.which("docker") is not None


def _run(cmd: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def measure_cold_start(image: str) -> dict[str, object]:
    if not _have_docker():
        return {"error": "docker not found"}
    name = f"yal-metrics-{int(time.time())}"
    start = time.perf_counter()
    proc = _run(
        [
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            name,
            image,
        ],
        timeout=180.0,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    if proc.returncode != 0:
        return {"error": proc.stderr.strip() or "docker run failed"}
    _run(["docker", "rm", "-f", name], timeout=60.0)
    return {"cold_start_ms": elapsed_ms, "image": image}


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure sandbox metrics for Step 9")
    parser.add_argument(
        "--image",
        default="node:22-slim",
        help="Container image to cold-start (default: node:22-slim proxy)",
    )
    args = parser.parse_args()
    results: dict[str, object] = {
        "host": shutil.which("docker") or "no-docker",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cold_start": measure_cold_start(args.image),
        "notes": [
            "Replace image with digest-pinned harness image for production numbers",
            "Run docker stats during an active turn for peak RSS",
            "E2B pause/resume: use vendor dashboard or API on staging project",
        ],
    }
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
