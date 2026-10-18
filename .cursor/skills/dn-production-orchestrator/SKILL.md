---
name: dn-production-orchestrator
description: >-
  Run the D0 Implement→Verify→Advance loop for production-grade Dn work
  (D8–D13 after D7). Use when the user asks to complete Dns, run the Dn
  series, orchestrate D8/D9/D10/D11/D12/D13, or continue the D5-style
  verification loop without MVP shortcuts.
---

# Dn production orchestrator (D0 loop)

## When this skill applies

User wants to **finish Dns production-grade** the way D5/D6 were done:
implement + acceptance tests → **fresh VERIFY subagent** → PASS → next Dn.
No MVP waivers unless dated residual with owner.

## Hard rules (never violate)

1. **No MVP** — do not mark Dn PASS while verify lists P0/P1 gaps. Prefer FULL reopen style (as D6 FULL).
2. **Verify does not implement** — always `Task` explore/generalPurpose with a **new** agent (no resume of implementer).
3. **Implement does not self-certify** — after material changes, always re-verify.
4. **Outer plane in scope** when Dn acceptance says so: `backend/prime_agent` + `frontend`.
5. **Regression FAIL** — if verify finds earlier Dn broken, fix before advancing.
6. **Human gate on Blocked** — secrets, product decisions, vendor keys: write residual; do not fake PASS.
7. Cap **5 verify FAIL cycles** per Dn, then stop and report (do not infinite loop).

## Paths

| Role | Absolute path |
|------|----------------|
| Guides | `/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/development/ai-backend-production-grade/` |
| D0 | `.../D0.md` |
| AI repo | `/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main` |
| BFF | `/Users/rahulrawatadmin/Downloads/prime-agent-study-main/backend` |
| FE | `/Users/rahulrawatadmin/Downloads/prime-agent-study-main/frontend` |
| State | `.../development/ai-backend-production-grade/orchestrator/state.json` |
| CLI | `.../development/ai-backend-production-grade/orchestrator/dn_orchestrator.py` |

Default series after D7: **D8 → D9 → D10 → D11 → D12 → D13 → LOCAL_DEMO**.

If D0 shows D7 still `REOPEN` without `FULL PASS`, **verify D7 FULL first** before D8.

## Startup (every session)

```bash
python3 .../orchestrator/dn_orchestrator.py status
python3 .../orchestrator/dn_orchestrator.py next-action
```

Follow the printed `action` and paste prompts from `emit-prompts`.

## Loop per Dn

### 0. Scope

Read `Dn.md`: Status, Acceptance, Definition of done, test matrix, § missing for production.
Set state: `python3 dn_orchestrator.py begin --dn D8`

### 1. IMPLEMENT (production-grade)

Spawn **one** `generalPurpose` Task (or work in-session) with the **Implement prompt** from:

`python3 dn_orchestrator.py emit-prompts --dn D8`

Implement agent must:

- Close Critical + High production rows (not MVP shortcut).
- Add/extend **blind** `dN_acceptance` suites (supervisor / gateway / memory / backend / frontend as needed).
- Run touched package tests when feasible.
- Update Dn Status only after verify PASS (or leave Status for orchestrator).
- **Not** claim PASS itself.

### 2. VERIFY (fresh subagent)

Always **new** Task `explore` (or generalPurpose), **VERIFY ONLY**:

Use the Verify prompt from `emit-prompts` (matches D0 §2).

Parse return for `## Verdict: PASS|FAIL`.

### 3. DECIDE

```bash
# on PASS:
python3 dn_orchestrator.py record-verify --dn D8 --verdict PASS --score 'N/M' --notes '...'

# on FAIL:
python3 dn_orchestrator.py record-verify --dn D8 --verdict FAIL --score 'N/M' --notes 'paste P0/P1 gaps'
```

- **FAIL** → implement **only** the gap list (highest severity first) → VERIFY again.
- **PASS** → append D0 status log row; set Dn.md Status PASS; `advance`; begin next Dn.
- **Blocked** → `record-verify --verdict BLOCKED` and stop series.

### 4. Advance

```bash
python3 dn_orchestrator.py advance   # only after PASS
python3 dn_orchestrator.py next-action
```

Repeat until D13 PASS, then run LOCAL_DEMO verify (separate guide).

## Subagent parallelism (allowed)

Within one IMPLEMENT cycle you may spawn in parallel:

- tests/acceptance author (write `dN_acceptance` first from acceptance matrix)
- AI-plane implementer
- BFF/FE implementer

Then **one** VERIFY after merges. Never parallel VERIFY claiming PASS.

## Stop conditions

- Series complete: D13 PASS + user asked for LOCAL_DEMO.
- `max_fail_cycles` hit for active Dn.
- User says stop / pause.
- BLOCKED residual written.

## Anti-patterns

- Marking acceptance `[x]` before verify PASS.
- Resuming the implementer as “verify”.
- Shipping mock FE for Memory/Budget/Subagents/upload while claiming Dn PASS (G6).
- Skipping D8–D12 to jump to D13 tracing.
- “MVP shortcut” in Status without D0 reopen + FULL PASS later.
