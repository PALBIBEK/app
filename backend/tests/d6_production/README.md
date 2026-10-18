# Independent D6 FULL-PRODUCTION acceptance (system BFF)

Authored against D6.md §4 High / §5 C / E / F production gaps the MVP left open:
HttpTool bind, no silent echo for endpoint-less customs, BFF approvals proxy,
Lab execute + SSRF-at-call.

Blind suite — intentional reds OK. Do not weaken assertions; do not edit D6.md.

```bash
cd backend
.venv/bin/pytest tests/d6_production -v --no-cov --rootdir=.
```
