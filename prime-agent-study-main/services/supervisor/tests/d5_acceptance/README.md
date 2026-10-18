# Independent D5 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D5.md` §6 Acceptance B–D,
§9 Test matrix T6–T10 / T12-adjacent, and §10 Definition of done — **not** from
existing product tests.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead.

Run:

```bash
/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor/.venv/bin/pytest \
  tests/d5_acceptance -v
```
