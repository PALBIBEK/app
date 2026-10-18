# Independent D13 acceptance (supervisor — production tracing)

Authored from `development/ai-backend-production-grade/D13.md` §5 A–B,
§8 test matrix T1–T5 / T8–T9, and Definition of done — **not** from existing
product unit/e2e suites.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — fix product code instead. Do **not** mark D13.md
checkboxes from this suite. Do **not** soften assertions to match global-projector
stubs or e2e-only Jaeger compose.

| # | Case | File |
|---|------|------|
| T1 | inbound `traceparent` → shared `trace_id` | `test_d13_t1_traceparent.py` |
| T2 | tool start/end paired `execute_tool` spans | `test_d13_t2_tool_spans.py` |
| T3 | OTLP down → run still completes | `test_d13_t3_otlp_soft_fail.py` |
| T4 | concurrent sessions → no projector cross-talk | `test_d13_t4_concurrent_sessions.py` |
| T5 | compose/docs Jaeger + `OTEL_EXPORTER_OTLP_ENDPOINT` | `test_d13_t5_compose_docs.py` |
| T6 | gateway child same trace (`traceparent`) | `test_d13_t6_gateway_traceparent.py` |
| T8 | chat content not in span attrs | `test_d13_t8_no_chat_content.py` |
| T9 | terminate reaps / `session_terminal` wired | `test_d13_t9_terminate_reap.py` |

Companion suites:

- `backend/tests/d13_acceptance/` — BFF `traceparent` + Trace API
- `frontend/src/d13_acceptance/` — `/traces` live sessions honesty

Run:

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d13_acceptance -v --rootdir=.
```
