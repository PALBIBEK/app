# Independent D13 acceptance (system BFF — tracing)

Authored from D13 §5 B–C / §8 T7 — AiClient injects `traceparent` toward
supervisor when context is present; Trace API (or Jaeger proxy) is
tenant-scoped. Do **not** edit to force green.

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/backend
.venv/bin/pytest tests/d13_acceptance -v --no-cov --rootdir=.
```
