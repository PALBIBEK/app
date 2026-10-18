# Independent D7 acceptance (Durable Memory Service)

Authored from D7 §5 A / C and matrix T1–T3, T9–T10, T15.

Blind suite — fix product code, not these tests.

Run (reuse a venv with memory_service + pytest; example using /tmp):

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/memory
PYTHONPATH=src /tmp/yal-memory-venv-d7/bin/pytest tests/d7_acceptance -v --rootdir=.
```
