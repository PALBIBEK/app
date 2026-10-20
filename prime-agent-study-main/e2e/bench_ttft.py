"""Time to first token on a running stack: chat-only turns and tool turns, p50 and p95.

    e2e/bench_ttft.py [sessions per kind, default 25] [composition, default fx-full]

(``fx-full`` is the local_process composition stage 4 publishes for tenant t1.)

Creates sessions on a githi composition, sends one message each, and reports two
times: **start → first token**, from the create request to the first assistant
``message_end`` (what a new session's user waits; the article's measure, since a
sandbox used to be built at create), and **run → first token**, from the run's
``run_start`` (the supervisor's /metrics measure). Run it before and after a
change, on the same stack, to compare. With the local_process provider a sandbox
costs a directory; with Docker or E2B it costs a container boot, which is what
lazy provisioning takes off a chat-only turn.
"""

from __future__ import annotations

import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from client import User, runs_ended

user = User("t1", "bench")


def ttft(evs: list[dict]) -> int | None:
    start = next((e["ts_ms"] for e in evs if e["type"] == "run_start"), None)
    answer = next(
        (e["ts_ms"] for e in evs if e["type"] == "message_end" and e["data"].get("role") == "assistant"),
        None,
    )
    return answer - start if start is not None and answer is not None else None


def one(composition: tuple[str, str], text: str) -> tuple[int, int] | None:
    began = int(time.time() * 1000)
    r = user.create(*composition)
    assert r.status_code == 201, (r.status_code, r.text[:200])
    sid = r.json()["session_id"]
    assert user.say(sid, text).status_code == 202
    evs = user.wait(sid, runs_ended(1), timeout=120)
    run = ttft(evs)
    answer = next(
        (e["ts_ms"] for e in evs if e["type"] == "message_end" and e["data"].get("role") == "assistant"),
        None,
    )
    return (answer - began, run) if run is not None and answer is not None else None


def pct(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 25
    latest = user.latest("composition", sys.argv[2] if len(sys.argv) > 2 else "fx-full")
    composition = (latest["artifact_id"], latest["digest"])
    for label, text in (("chat-only", "hello there"), ("one tool", '/tool ls {"path": "."}')):
        started = time.time()
        with ThreadPoolExecutor(max_workers=5) as pool:
            got = [v for v in pool.map(one, [composition] * n, [text] * n) if v is not None]
        start = [g[0] for g in got]
        run = [g[1] for g in got]
        print(
            f"{label:10} runs={len(got):3}"
            f"  start→token p50={pct(start, 0.5):5} p95={pct(start, 0.95):5} ms"
            f"  run→token p50={pct(run, 0.5):5} p95={pct(run, 0.95):5} ms"
            f"  ({time.time() - started:.0f}s)"
        )


if __name__ == "__main__":
    main()
