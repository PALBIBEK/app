# Independent D6 FULL-PRODUCTION acceptance (supervisor)

Authored against D6.md **§4 High / Critical** production gaps that the MVP
shortcut left open (echo-shaped custom tools, trimmed coding-tools without
contract deferral, HttpTool/SSRF, Composite `_tool_set` under MCP wrap).

**Blind suite** — intentional reds are expected until production closes.
Do **not** weaken assertions to green today’s MVP. Do **not** edit D6.md
checkboxes from this suite. Prefer fixing product code.

Run:

```bash
cd services/supervisor
.venv/bin/pytest tests/d6_production -v --rootdir=.
```

Does not replace `tests/d6_acceptance` (MVP greens stay separate).
