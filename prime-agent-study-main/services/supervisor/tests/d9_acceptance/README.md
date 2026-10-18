# Independent D9 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D9.md` Acceptance §5 A–E
and Definition of done — **not** from existing product unit tests.

Blind suite: restart survival with durable SoT, hydrate + CAS fail-closed,
compose consumes postgres/object-store (no silent memory in staging).

Do **not** edit these tests to force green — fix product code instead.
Do **not** mark D9.md Status PASS from this suite.

Companion suites:

- `backend/tests/d9_acceptance/` — BFF durable file proxy + grants
- `frontend/src/d9_acceptance/` — Test upload live + naming honesty (G6)

Run:

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d9_acceptance -v --rootdir=.
```
