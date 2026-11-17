"""The browser agent's A/B battery on a running stack: Hermes's ``evals/browser_use``.

    e2e/bench_browser.py run [--tasks easy|hard|PATH] [--arms githi-browser-v1,...]
                             [--models m1,m2] [--reps 3] [--results PATH] [--timeout 1200]
    e2e/bench_browser.py report [PATH ...]

Hermes's design, on our harness. **Arms differ only by composition**: each arm is a
composition the stack offers (its latest version), so an A/B is two compositions that
differ in what is under test (a tool set, a prompt pack, a sandbox profile and its
browser). **Tasks are oracle-checked**: Hermes's batteries, verbatim (``browser_bench/
tasks``): toscrape-family sites (stable content, no anti-bot), and regexes over the
final answer (``oracle_all`` every one, ``oracle_any`` one of them). **Resume-safe**: a
cell (arm, task, model, rep) already in the results file is skipped, so a killed
battery continues where it stopped.

Each cell is a fresh session on the arm's composition with the model (``model`` on
create, any the stack routes), one message, and one run: its tokens (prompt and
completion, every model call of the run), model and tool calls (with their names, to
audit a shortcut around the browser), wall clock, and the answer's last 400 characters.

The run costs model tokens: Hermes's first run of this battery (6 hard tasks x 3 reps,
2 models, 3 arms) was 108 cells, at roughly 20-65k tokens a cell.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from client import User, payloads, runs_ended

HERE = Path(__file__).resolve().parent
TASKS = HERE / "browser_bench" / "tasks"
RESULTS = HERE / "browser_bench" / "results" / "results.jsonl"


def tasks_at(name: str) -> dict[str, dict[str, Any]]:
    path = TASKS / f"{name}.json" if name in ("easy", "hard") else Path(name)
    return json.loads(path.read_text(encoding="utf-8"))


def answered(task: dict[str, Any], text: str) -> bool:
    """Hermes's oracle: every ``oracle_all`` literal, else any ``oracle_any`` one, in the
    answer, case-insensitively."""
    found = [re.search(re.escape(x), text, re.IGNORECASE) for x in task.get("oracle_all") or task.get("oracle_any", [])]
    return bool(found) and (all(found) if task.get("oracle_all") else any(found))


def final_text(run: list[dict[str, Any]]) -> str:
    finals = [p for p in payloads(run, "MessageEnd") if p.get("role") == "assistant"]
    return " ".join(c.get("text", "") for c in finals[-1].get("content", []) if c.get("type") == "text") if finals else ""


def tokens(run: list[dict[str, Any]]) -> tuple[int, int]:
    """Prompt and completion tokens over the run's model calls."""
    usage = [p.get("usage") or {} for p in payloads(run, "MessageEnd") if p.get("role") == "assistant"]
    return sum(int(u.get("prompt_tokens") or 0) for u in usage), sum(int(u.get("completion_tokens") or 0) for u in usage)


def cell(user: User, composition: dict[str, Any], arm: str, key: str, task: dict[str, Any], model: str, rep: int,
         timeout: float) -> dict[str, Any]:
    """One run of ``task``: what ``report`` aggregates."""
    began = time.time()
    out: dict[str, Any] = {"arm": arm, "task": key, "model": model, "rep": rep, "error": None}
    final, run = "", []
    try:
        r = user.create(composition["artifact_id"], composition["digest"], model=model)
        if r.status_code != 201:
            raise RuntimeError(f"create {r.status_code}: {r.text[:200]}")
        sid = out["session_id"] = r.json()["session_id"]
        if (said := user.say(sid, task["prompt"])).status_code != 202:
            raise RuntimeError(f"message {said.status_code}: {said.text[:200]}")
        run = user.wait(sid, runs_ended(1), timeout=timeout)
        final = final_text(run)
        if not final and (failed := [e for e in run if "error" in e.get("type", "")]):  # the run failed, not the answer
            out["error"] = f"{failed[-1]['type']}: {json.dumps(failed[-1].get('data'))[:200]}"
    except Exception as exc:  # noqa: BLE001 — a cell's failure is its row, the battery goes on
        out["error"] = f"{type(exc).__name__}: {exc}"
    prompt, completion = tokens(run)
    names = [p["tool_name"] for p in payloads(run, "ToolStart")]
    return out | {
        "ok": bool(final) and answered(task, final) and out["error"] is None,
        "wall_s": round(time.time() - began, 1),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "api_calls": len([p for p in payloads(run, "MessageEnd") if p.get("role") == "assistant"]),
        "tool_calls": len(names),
        "tool_call_names": names,
        "final_snippet": final[-400:],
    }


def run(args: argparse.Namespace) -> None:
    battery = tasks_at(args.tasks)
    results = Path(args.results)
    results.parent.mkdir(parents=True, exist_ok=True)
    done: set[tuple[str, str, str, int]] = set()
    if results.exists():
        for line in results.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done.add((row["arm"], row["task"], row["model"], int(row["rep"])))
            except (ValueError, KeyError):
                continue
    user = User(args.tenant, args.principal)
    arms = {arm: user.latest("composition", arm) for arm in args.arms.split(",")}
    cells = [(a, t, m, r) for m in args.models.split(",") for a in arms for t in battery for r in range(1, args.reps + 1)]
    todo = [c for c in cells if c not in done]
    print(f"{len(cells)} cells, {len(cells) - len(todo)} already done", flush=True)
    for n, (arm, key, model, rep) in enumerate(todo, 1):
        row = cell(user, arms[arm], arm, key, battery[key], model, rep, args.timeout)
        with results.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        mark = "ok " if row["ok"] else "FAIL"
        print(f"[{n}/{len(todo)}] {mark} {model} {arm} {key} rep{rep}: {row['total_tokens']} tok, "
              f"{row['tool_calls']} calls, {row['wall_s']}s{'  ' + row['error'] if row['error'] else ''}", flush=True)


def report(paths: list[str]) -> None:
    """Hermes's scorecard: per (model, arm), ok rate, tokens (mean, median), tool calls and
    wall clock over the cells that passed, and tokens against the first arm."""
    rows = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    if not rows:
        print("no rows")
        return
    cells: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    first_arm: dict[str, str] = {}
    for row in rows:
        cells[(row.get("model", "?"), row.get("arm", "?"))].append(row)
        first_arm.setdefault(row.get("model", "?"), row.get("arm", "?"))
    base = {
        model: statistics.mean(r["total_tokens"] for r in oks)
        for (model, arm), rs in cells.items()
        if arm == first_arm[model] and (oks := [r for r in rs if r.get("ok")])
    }
    head = f"{'model':<34} {'arm':<24} {'ok':>7} {'tok_mean':>9} {'tok_med':>8} {'calls':>6} {'wall_s':>7} {'vs first':>8}"
    print(head)
    print("-" * len(head))
    for model, arm in sorted(cells):
        rs = cells[(model, arm)]
        oks = [r for r in rs if r.get("ok")]
        toks = [r.get("total_tokens", 0) for r in oks]
        mean = statistics.mean(toks) if toks else 0
        delta = f"{(mean - base[model]) / base[model] * 100:+.0f}%" if arm != first_arm[model] and base.get(model) and mean else ""
        print(
            f"{model:<34} {arm:<24} {len(oks):>3}/{len(rs):<3} {mean:>9.0f} {statistics.median(toks) if toks else 0:>8.0f} "
            f"{statistics.mean(r.get('tool_calls', 0) for r in oks) if oks else 0:>6.1f} "
            f"{statistics.mean(r.get('wall_s', 0) for r in oks) if oks else 0:>7.1f} {delta:>8}"
        )
    if errors := [r for r in rows if r.get("error")]:
        print(f"\nerrors: {len(errors)}")
        for r in errors[:10]:
            print(f"  {r.get('model')}/{r.get('arm')}/{r.get('task')}/rep{r.get('rep')}: {r['error'][:120]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--tasks", default="hard")
    go.add_argument("--arms", default="githi-browser-v1")
    go.add_argument("--models", required=True)
    go.add_argument("--reps", type=int, default=3)
    go.add_argument("--results", default=str(RESULTS))
    go.add_argument("--timeout", type=float, default=1200)
    go.add_argument("--tenant", default="t1")
    go.add_argument("--principal", default="bench")
    show = sub.add_parser("report")
    show.add_argument("paths", nargs="*", default=[str(RESULTS)])
    args = parser.parse_args()
    if args.command == "run":
        run(args)
    else:
        report(args.paths)


if __name__ == "__main__":
    sys.exit(main())
