# Independent D7 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D7.md` Acceptance §5 A–E,
§8 test matrix T1–T15 (ContextMemory + durable glue + boundaries), and Definition
of done — **not** from existing product tests.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead. Do **not** mark D7.md
checkboxes from this suite.

Companion suites:

- `services/memory/tests/d7_acceptance/` — Durable Memory HTTP (auth, IDOR, readyz, Tier ops)
- `backend/tests/d7_acceptance/` — outer BFF / harness honesty
- `frontend/src/d7_acceptance/` — `/memory` live-or-not-theater

Run:

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d7_acceptance -v --rootdir=.
```
