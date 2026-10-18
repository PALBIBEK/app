# Independent D7 FULL-PRODUCTION acceptance (supervisor / deploy)

Authored against D7 REOPENED deploy gaps: root `docker-compose.yml` must include
the Memory service (preferred over staging-only).

**Blind suite** — intentional reds OK until production closes.
Does not replace `tests/d7_acceptance/` (MVP greens).

Run:

```bash
cd services/supervisor
.venv/bin/pytest tests/d7_production -v --rootdir=.
```
