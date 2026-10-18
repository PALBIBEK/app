# Independent D5 acceptance (frontend product honesty)

Authored from `development/ai-backend-production-grade/D5.md` §6 Acceptance D.
Primary static scans live under supervisor `tests/d5_acceptance/test_d5_product_secrets.py`.

This directory marks the FE acceptance lane. Prefer those pytest static asserts;
add vitest here only if Settings BYOK/ops-configured UI needs DOM-level checks later.

Implementation agents must **not** edit acceptance tests to force green.
