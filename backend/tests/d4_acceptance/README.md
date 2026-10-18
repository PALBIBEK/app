# Independent D4 acceptance (system BFF)

Authored from `development/ai-backend-production-grade/D4.md` §5 G and §9 Test matrix T12–T15 (plus abort/terminate / command_id / no user JWT forwarding).

These tests are a **blind acceptance suite**. Implementation agents must **not** edit them to force green — fix product code instead.

Run:

```bash
/Users/rahulrawatadmin/Downloads/prime-agent-study-main/backend/.venv/bin/pytest \
  tests/d4_acceptance -v --no-cov
```
