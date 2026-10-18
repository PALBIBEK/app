# Dn production orchestrator (D8→D13)

Automates the **bookkeeping** of the D0 loop. A **Cursor agent** still does
implement + spawn verify subagents (bash cannot call `Task`).

## What this is

```text
you / Cursor agent
    │
    ├─ dn_orchestrator.py next-action   → BEGIN | IMPLEMENT | VERIFY | ADVANCE
    ├─ emit-prompts                     → exact Task prompts
    ├─ implement (subagents + tests)    → production-grade, not MVP
    ├─ verify (NEW subagent)            → PASS | FAIL
    ├─ record-verify                    → updates state + D0 log
    └─ advance → next Dn … → D13 → LOCAL_DEMO
```

## Quick start

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/development/ai-backend-production-grade/orchestrator

python3 dn_orchestrator.py next-action
```

In Cursor chat, say:

> Use the `dn-production-orchestrator` skill. Run the D0 loop from next-action through D13, production-grade only.

Or manually:

```bash
# If D7 still REOPEN without FULL PASS — finish D7 first
python3 dn_orchestrator.py emit-prompts --dn D7
# … implement → verify FULL …
python3 dn_orchestrator.py gate-d7-clear

python3 dn_orchestrator.py begin --dn D8
python3 dn_orchestrator.py emit-prompts --dn D8
# Task implement → Task verify (new agent) …
python3 dn_orchestrator.py record-verify --dn D8 --verdict PASS --score 'N/M' --notes '...'
python3 dn_orchestrator.py advance
```

## Skill

Project skill: `.cursor/skills/dn-production-orchestrator/SKILL.md`

## Limits

| Can | Cannot |
|-----|--------|
| Track Dn / cycle / max fails | Spawn Cursor Task tools alone |
| Emit D0-faithful prompts | Replace human judgment on BLOCKED |
| Append D0 status rows on record-verify | Guarantee overnight unattended completion |

Unattended full D8–D13 still needs a long Cursor agent session (or Cloud Agent) that follows the skill and calls this CLI between cycles.
