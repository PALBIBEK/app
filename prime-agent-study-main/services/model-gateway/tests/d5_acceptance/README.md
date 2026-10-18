# Independent D5 acceptance (model-gateway)

Authored from `development/ai-backend-production-grade/D5.md` §6 Acceptance A–D,
§9 Test matrix T1–T12, and §10 Definition of done — **not** from existing product tests.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead.

Run:

```bash
/Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/model-gateway/.venv/bin/pytest \
  tests/d5_acceptance -v
```
