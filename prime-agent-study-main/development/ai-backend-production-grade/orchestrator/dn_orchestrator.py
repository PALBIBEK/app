#!/usr/bin/env python3
"""D0-style Dn series state machine (D8→D13 production-grade).

This does NOT spawn Cursor subagents by itself. It tracks state and emits the
exact Implement / Verify prompts for a Cursor agent (or human) that runs the
Task tool loop: implement → fresh verify → advance.

Usage:
  python3 dn_orchestrator.py status
  python3 dn_orchestrator.py next-action
  python3 dn_orchestrator.py begin --dn D8
  python3 dn_orchestrator.py emit-prompts --dn D8
  python3 dn_orchestrator.py record-verify --dn D8 --verdict PASS --score '12/12' --notes '...'
  python3 dn_orchestrator.py advance
  python3 dn_orchestrator.py gate-d7-clear   # after D7 FULL PASS
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
GUIDES = HERE.parent
STATE_PATH = HERE / "state.json"
D0_PATH = GUIDES / "D0.md"

AI_ROOT = Path(
    "/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main"
)
BACKEND = Path("/Users/rahulrawatadmin/Downloads/prime-agent-study-main/backend")
FRONTEND = Path("/Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend")

DEFAULT_SERIES = ["D8", "D9", "D10", "D11", "D12", "D13"]


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {
            "series": list(DEFAULT_SERIES),
            "active_dn": None,
            "phase": "idle",
            "cycle": 0,
            "max_fail_cycles": 5,
            "require_d7_full_gate": True,
            "history": [],
            "last_verify": None,
            "notes": "",
        }
    return json.loads(STATE_PATH.read_text())


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def guide_path(dn: str) -> Path:
    if dn == "LOCAL_DEMO":
        return GUIDES / "LOCAL_DEMO.md"
    return GUIDES / f"{dn}.md"


def d7_needs_full_gate(state: dict[str, Any]) -> bool:
    if not state.get("require_d7_full_gate", True):
        return False
    text = D0_PATH.read_text() if D0_PATH.exists() else ""
    # FULL PASS for D7 present?
    if re.search(r"\|\s*D7\s*\|\s*FULL\s*\|\s*PASS", text, re.I):
        return False
    # Still REOPEN without later FULL PASS
    if re.search(r"\|\s*D7\s*\|\s*REOPEN", text, re.I):
        return True
    d7 = (GUIDES / "D7.md").read_text() if (GUIDES / "D7.md").exists() else ""
    if re.search(r"REOPEN", d7[:400], re.I) and not re.search(
        r"FULL.*PASS|PASS.*full production", d7[:500], re.I
    ):
        return True
    return False


def cmd_status(_: argparse.Namespace) -> None:
    state = load_state()
    print(json.dumps(state, indent=2))
    print(f"\nD7 full gate needed: {d7_needs_full_gate(state)}")


def cmd_next_action(_: argparse.Namespace) -> None:
    state = load_state()
    if d7_needs_full_gate(state):
        print(
            json.dumps(
                {
                    "action": "GATE_D7_FULL",
                    "reason": "D0/D7 show REOPEN without FULL PASS — finish D7 production before D8",
                    "guide": str(GUIDES / "D7.md"),
                    "hint": "Run emit-prompts --dn D7 then implement→verify FULL; then gate-d7-clear",
                },
                indent=2,
            )
        )
        return

    active = state.get("active_dn")
    phase = state.get("phase", "idle")
    cycle = int(state.get("cycle", 0))
    max_fail = int(state.get("max_fail_cycles", 5))

    if phase == "blocked":
        print(
            json.dumps(
                {
                    "action": "STOP_BLOCKED",
                    "active_dn": active,
                    "last_verify": state.get("last_verify"),
                },
                indent=2,
            )
        )
        return

    if phase == "failed_cap":
        print(
            json.dumps(
                {
                    "action": "STOP_MAX_CYCLES",
                    "active_dn": active,
                    "cycle": cycle,
                    "max_fail_cycles": max_fail,
                    "last_verify": state.get("last_verify"),
                },
                indent=2,
            )
        )
        return

    if active is None or phase == "idle":
        series = state.get("series") or DEFAULT_SERIES
        done = {h["dn"] for h in state.get("history", []) if h.get("verdict") == "PASS"}
        nxt = next((d for d in series if d not in done), None)
        if nxt is None:
            print(
                json.dumps(
                    {
                        "action": "SERIES_COMPLETE",
                        "next": "LOCAL_DEMO",
                        "guide": str(GUIDES / "LOCAL_DEMO.md"),
                    },
                    indent=2,
                )
            )
            return
        print(
            json.dumps(
                {
                    "action": "BEGIN",
                    "dn": nxt,
                    "command": f"python3 {STATE_PATH.parent / 'dn_orchestrator.py'} begin --dn {nxt}",
                    "then": f"python3 {STATE_PATH.parent / 'dn_orchestrator.py'} emit-prompts --dn {nxt}",
                },
                indent=2,
            )
        )
        return

    if phase in ("implement", "fix"):
        print(
            json.dumps(
                {
                    "action": "IMPLEMENT",
                    "dn": active,
                    "cycle": cycle,
                    "gaps": (state.get("last_verify") or {}).get("notes"),
                    "emit": f"python3 {STATE_PATH.parent / 'dn_orchestrator.py'} emit-prompts --dn {active}",
                },
                indent=2,
            )
        )
        return

    if phase == "verify":
        print(
            json.dumps(
                {
                    "action": "VERIFY",
                    "dn": active,
                    "cycle": cycle,
                    "emit": f"python3 {STATE_PATH.parent / 'dn_orchestrator.py'} emit-prompts --dn {active}",
                    "note": "Spawn a NEW verify Task; do not resume implementer",
                },
                indent=2,
            )
        )
        return

    if phase == "passed":
        print(
            json.dumps(
                {
                    "action": "ADVANCE",
                    "dn": active,
                    "command": f"python3 {STATE_PATH.parent / 'dn_orchestrator.py'} advance",
                },
                indent=2,
            )
        )
        return

    print(json.dumps({"action": "UNKNOWN", "state": state}, indent=2))


def cmd_begin(args: argparse.Namespace) -> None:
    state = load_state()
    dn = args.dn.upper()
    if dn != "D7" and d7_needs_full_gate(state):
        raise SystemExit("Refuse begin: clear D7 FULL gate first (gate-d7-clear after PASS)")
    state["active_dn"] = dn
    state["phase"] = "implement"
    state["cycle"] = 0
    state["last_verify"] = None
    save_state(state)
    print(json.dumps({"ok": True, "active_dn": dn, "phase": "implement"}, indent=2))


def implement_prompt(dn: str, gaps: str | None) -> str:
    g = guide_path(dn)
    gap_block = (
        f"\nPrior verify gaps (implement ONLY these, highest severity first):\n{gaps}\n"
        if gaps
        else "\nFirst pass: close ALL Critical + High production rows in the guide (no MVP shortcut).\n"
    )
    return f"""IMPLEMENT — production-grade {dn} (not MVP).

Guide: {g}
Acceptance: sections "Acceptance criteria" + "Definition of done" + test matrix + "What is missing for production" (Critical/High).
Also obey D0 global invariants G1–G7:
{GUIDES / "D0.md"}

Repo roots:
- AI: {AI_ROOT}
- Outer BFF: {BACKEND}
- Frontend: {FRONTEND}

Rules:
1. Production-grade only — no MVP waivers unless you write a dated residual with owner in the Dn guide AND leave Status non-PASS.
2. Add/extend blind dN_acceptance tests (supervisor/memory/gateway/backend/frontend as the guide requires) BEFORE or WITH implementation so verify has evidence.
3. Prefer patterns from D5/D6 FULL: suites under tests/dN_acceptance/, BFF honesty, no mock live nav (G6).
4. Run touched package tests when feasible; fix regressions of earlier Dns.
5. Do NOT mark Dn Status PASS yourself — stop after code+tests; orchestrator will VERIFY.
6. Do NOT edit D0 status log except if explicitly asked after verify PASS.
{gap_block}
Return when done:
## Implemented
## Tests added/updated (paths)
## Residual risks (dated or none)
## Ready for verify: yes
"""


def verify_prompt(dn: str) -> str:
    g = guide_path(dn)
    return f"""VERIFY ONLY — do not edit code.

Scope: {dn} (production-grade / FULL — reject MVP shortcuts)
Guide: {g}
Acceptance source: sections "Acceptance criteria" + "Definition of done" (+ test matrix if present).
Also check D0 invariants G1–G7 when relevant: {GUIDES / "D0.md"}

Repo roots:
- AI: {AI_ROOT}
- Outer: {BACKEND} and {FRONTEND}

Return markdown:
## Verdict: PASS | FAIL
## Score: N/M acceptance rows green
## Green (row → evidence path)
## Missing / Partial (row → what's wrong → severity P0|P1|P2)
## Regressions (if any)
## Suggested next implement slice (max 5 bullets)

Be strict. Stub/env-default-unsafe = not green. Silent mock on live nav = FAIL (G6).
MVP-only (UI disabled, shared token, trimmed Critical rows without contract deferral) = FAIL for FULL.
"""


def cmd_emit_prompts(args: argparse.Namespace) -> None:
    state = load_state()
    dn = (args.dn or state.get("active_dn") or "").upper()
    if not dn:
        raise SystemExit("Pass --dn or begin first")
    gaps = None
    lv = state.get("last_verify")
    if lv and lv.get("verdict") == "FAIL":
        gaps = lv.get("notes")
    print("========== IMPLEMENT PROMPT (Task generalPurpose) ==========")
    print(implement_prompt(dn, gaps))
    print("========== VERIFY PROMPT (NEW Task explore — do not resume implementer) ==========")
    print(verify_prompt(dn))


def append_d0_log(dn: str, cycle: int, verdict: str, score: str, notes: str) -> None:
    if not D0_PATH.exists():
        return
    today = date.today().isoformat()
    # D0 table uses 2026-08-31 style in this repo's fiction clock; prefer today.
    row = f"| {today} | {dn} | {cycle} | {verdict} {score} | {notes[:120]} |\n"
    text = D0_PATH.read_text()
    marker = "\n---\n\n## 7. Related"
    if marker in text:
        text = text.replace(marker, row + marker)
        D0_PATH.write_text(text)


def update_dn_status(dn: str, line: str) -> None:
    path = guide_path(dn)
    if not path.exists() or dn == "LOCAL_DEMO":
        return
    lines = path.read_text().splitlines(True)
    for i, ln in enumerate(lines[:15]):
        if ln.startswith("**Status:**"):
            lines[i] = line if line.endswith("\n") else line + "\n"
            path.write_text("".join(lines))
            return


def cmd_record_verify(args: argparse.Namespace) -> None:
    state = load_state()
    dn = (args.dn or state.get("active_dn") or "").upper()
    verdict = args.verdict.upper()
    score = args.score or ""
    notes = args.notes or ""
    cycle = int(state.get("cycle", 0))
    max_fail = int(state.get("max_fail_cycles", 5))

    entry = {
        "dn": dn,
        "cycle": cycle,
        "verdict": verdict,
        "score": score,
        "notes": notes,
        "date": date.today().isoformat(),
    }
    state["last_verify"] = entry
    state.setdefault("history", []).append(entry)

    if verdict == "PASS":
        state["phase"] = "passed"
        append_d0_log(dn, cycle, "PASS", score, notes or "orchestrator")
        update_dn_status(
            dn,
            f"**Status:** PASS ({date.today().isoformat()}) — verify cycle {cycle}; {score}; next orchestrator advance  \n",
        )
    elif verdict == "BLOCKED":
        state["phase"] = "blocked"
        append_d0_log(dn, cycle, "BLOCKED", score, notes)
    else:
        # FAIL
        cycle += 1
        state["cycle"] = cycle
        if cycle >= max_fail:
            state["phase"] = "failed_cap"
            append_d0_log(dn, cycle, "FAIL_CAP", score, notes)
        else:
            state["phase"] = "fix"
            append_d0_log(dn, cycle - 1, "FAIL", score, notes)

    state["active_dn"] = dn
    save_state(state)
    print(json.dumps({"ok": True, "state_phase": state["phase"], "cycle": state["cycle"]}, indent=2))


def cmd_advance(_: argparse.Namespace) -> None:
    state = load_state()
    if state.get("phase") != "passed":
        raise SystemExit(f"Cannot advance: phase={state.get('phase')} (need passed)")
    done_dn = state.get("active_dn")
    series = state.get("series") or DEFAULT_SERIES
    passed = {h["dn"] for h in state.get("history", []) if h.get("verdict") == "PASS"}
    if done_dn:
        passed.add(done_dn)
    nxt = next((d for d in series if d not in passed), None)
    state["active_dn"] = None
    state["phase"] = "idle"
    state["cycle"] = 0
    state["last_verify"] = None
    save_state(state)
    print(
        json.dumps(
            {
                "ok": True,
                "completed": done_dn,
                "next_dn": nxt or "LOCAL_DEMO",
                "hint": "python3 dn_orchestrator.py next-action",
            },
            indent=2,
        )
    )


def cmd_gate_d7_clear(_: argparse.Namespace) -> None:
    state = load_state()
    state["require_d7_full_gate"] = False
    save_state(state)
    print(json.dumps({"ok": True, "require_d7_full_gate": False}, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="Dn D0 orchestrator state machine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Print state.json")
    sub.add_parser("next-action", help="What the Cursor agent should do next")
    b = sub.add_parser("begin", help="Start a Dn implement cycle")
    b.add_argument("--dn", required=True)
    e = sub.add_parser("emit-prompts", help="Print Implement + Verify prompts")
    e.add_argument("--dn", default=None)
    r = sub.add_parser("record-verify", help="Record verify result")
    r.add_argument("--dn", default=None)
    r.add_argument("--verdict", required=True, choices=["PASS", "FAIL", "BLOCKED", "pass", "fail", "blocked"])
    r.add_argument("--score", default="")
    r.add_argument("--notes", default="")
    sub.add_parser("advance", help="After PASS, clear active and prepare next Dn")
    sub.add_parser("gate-d7-clear", help="Mark D7 FULL gate cleared")

    args = p.parse_args()
    {
        "status": cmd_status,
        "next-action": cmd_next_action,
        "begin": cmd_begin,
        "emit-prompts": cmd_emit_prompts,
        "record-verify": cmd_record_verify,
        "advance": cmd_advance,
        "gate-d7-clear": cmd_gate_d7_clear,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
