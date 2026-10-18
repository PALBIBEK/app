# Independent D14 acceptance (supervisor — real local sandbox image)

Authored from `development/ai-backend-production-grade/D14.md` §4 A–C,
§5 test matrix T1–T5, and Definition of done.

These tests are a **blind acceptance suite**. Implementation agents must **not**
edit them to force green — build + re-pin a real image and add honest
placeholder detection instead. Do **not** soft-fallback to `local_process` on
`aaaa…` digests to make this suite pass.

| # | Case | File |
|---|------|------|
| T1 | `coding-docker-v1.json` digest not placeholder | `test_d14_t1_profile_digest.py` |
| T2 | `coding-v1.yaml` matches profile (same **real** digest) | `test_d14_t2_yaml_matches_profile.py` |
| T3 | pin is `sha256:` hex + not placeholder; optional `docker image inspect` | `test_d14_t3_pin_format_and_inspect.py` |
| T4 | resolve(coding pins) must not hand `local_docker` a placeholder | `test_d14_t4_resolve_no_placeholder.py` |
| T5 | product guard / catalog detection covers `aaaa…` (or d14 guard) | `test_d14_t5_placeholder_rejected.py` |

## Live create (VERIFY)

Suite T4 asserts the **resolve path** fails closed on placeholder pins. Full D14
VERIFY still requires a **live** `POST /v1/sessions` (bound `coding-v1`,
`SANDBOX_PROVIDER=local_docker`, docker.sock) returning **201** with an active
sandbox — not 503 image/placeholder failure. Do not treat this suite alone as
LOCAL_DEMO row 5 complete without that live create.

## Run

```bash
cd /Users/rahulrawatadmin/Downloads/prime-agent-study-main/prime-agent-study-main/services/supervisor
.venv/bin/pytest tests/d14_acceptance -v --rootdir=.
```

Expected **today** (placeholder `sha256:aaaa…` still pinned): suite **FAIL**.
