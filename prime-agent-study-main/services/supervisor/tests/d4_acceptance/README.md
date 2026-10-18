# Independent D4 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D4.md` §5 Acceptance, §9 Test matrix T1–T11 / T16–T17, and §10 Definition of done.

These tests are a **blind acceptance suite**. Implementation agents must **not** edit them to force green — fix product code instead.

Run:

```bash
/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor/.venv/bin/pytest \
  tests/d4_acceptance -v
```
