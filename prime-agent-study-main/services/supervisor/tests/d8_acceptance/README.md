# Independent D8 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D8.md` Acceptance §5 A–E,
§8 test matrix T1–T12 / T17, and Definition of done — **not** from existing
product tests.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead. Do **not** mark D8.md
checkboxes from this suite.

Companion suites:

- `backend/tests/d8_acceptance/` — BFF approvals + 409 mapping
- `frontend/src/d8_acceptance/` — Test HITL + Settings Budget honesty
- `services/model-gateway/tests/d8_acceptance/` — VK budget enforce

Run:

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d8_acceptance -v --rootdir=.
```
