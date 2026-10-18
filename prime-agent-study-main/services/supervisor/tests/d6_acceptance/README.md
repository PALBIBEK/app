# Independent D6 acceptance (supervisor)

Authored from `development/ai-backend-production-grade/D6.md` Acceptance §5 A–H
(especially H / LOCAL_DEMO #3), §8 test matrix T1–T14, and Definition of done —
**not** from existing product tests.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead. Do **not** mark D6.md
checkboxes from this suite.

Run:

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d6_acceptance -v --rootdir=.
```
