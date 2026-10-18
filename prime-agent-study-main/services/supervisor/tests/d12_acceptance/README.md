# D12 blind acceptance — subagents + HA (T1–T13)

Independent pytest suite authored from
`development/ai-backend-production-grade/D12.md` §8 test matrix.

**Do not** edit these tests to force green — fix product code instead.
**Do not** soften assertions to match echo stubs / unpaired backgrounds /
process-local warm skips.

Run:

```bash
cd services/supervisor
.venv/bin/pytest tests/d12_acceptance -v --rootdir=.
```

Companion: outer FE/BFF honesty is D12 T14 (out of this suite).
