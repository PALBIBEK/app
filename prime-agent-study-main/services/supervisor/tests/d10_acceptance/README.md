# D10 blind acceptance — sandbox isolation matrix T1–T14

Independent pytest suite; does not edit product code to force green.

Run:

```bash
cd services/supervisor && PYTHONPATH=src pytest tests/d10_acceptance -q
```
