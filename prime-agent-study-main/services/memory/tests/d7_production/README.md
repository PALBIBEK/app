# Independent D7 FULL-PRODUCTION acceptance (Durable Memory)

Authored against D7 REOPENED production gaps that the MVP shortcut left open
(shared-token primary auth, missing rate limits).

**Blind suite** — intentional reds are expected until production closes.
Do **not** weaken assertions to green today’s MVP. Do **not** edit D7.md.
Prefer fixing product code. MVP greens stay in `tests/d7_acceptance/`.

Covers:

1. JWT `tenant_id` claim path is preferred; `X-Tenant-Id` cannot override JWT tenant
2. Static `MEMORY_SERVICE_TOKEN` alone is insufficient as the primary staging path
3. Rate-limit header or middleware on memory mutating routes

Run:

```bash
cd services/memory
PYTHONPATH=src /tmp/yal-memory-venv-d7/bin/pytest tests/d7_production -v --rootdir=.
```
