# Local demo north star

**Status:** Normative gate — required after D1–D13 (does not replace per-Dn PASS)  
**Audience:** Implementers finishing D5→D13 on a laptop  
**Related:** [D0.md](./D0.md) verify loop · [README.md](./README.md) · [M0.md](../githi-to-services-migration/M0.md)

## Done means

> On a laptop: `docker compose up` brings a healthy AI plane. In product UI (BFF+FE only — never browser→supervisor): create **one** harness and bind it; create **one** custom tool and attach it to that harness; spawn **one** subagent and see child events; **upload** a file into the session workspace; **Test chat** so the agent can `read` that file; open **Traces** for that session and see ordered journal events (plus Jaeger for the same run when D13 local slice is claimed). No mock success on any of these surfaces.

Dn PASS alone is **not** demo Done. Demo Done = this checklist green under a fresh verify subagent.

---

## Checklist (capability → Dn → verify)

| # | Capability | Primary Dn | Verify (evidence) |
|---|------------|------------|-------------------|
| 1 | AI plane up locally | **D2** | Documented compose → supervisor `/readyz` + BFF `platform/status` → `run_allowed=true` |
| 2 | Create + bind one harness | **D3** | FE `/harness/new` → bind → Test enabled |
| 3 | Write own tool | **D6** | FE `/tools/new` live CRUD → attach UUID → session `list_tools` includes it → invoke in Test |
| 4 | Integrate subagents | **D12** | BFF spawn/list live; Test shows `subagent_*` chips; `/subagents` not mock |
| 5 | Upload files to workspace | **D9** | FE upload → BFF → durable scope; file listed |
| 6 | Chat with harness **against** workspace | **D4+D5+D6+D9** | Session create sends `scope_id`; hydrate; agent `read` cites uploaded content (non-echo model when keys present) |
| 7 | See traces for one session | **D4** (+ **D13** slice) | `/traces` → session event timeline required; Jaeger shows same run (D13 A); ClickHouse optional for demo |

### D13 local Jaeger slice (row 7)

Root `docker-compose.yml` (and staging) ship **Jaeger all-in-one** (`4317` OTLP gRPC, UI `http://127.0.0.1:16686`). Supervisor exports when:

| Env | Default (compose) | Notes |
|-----|-------------------|--------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://jaeger:4317` | Soft-fail if collector/Jaeger is down — agent runs still complete |
| `YAL_OTEL_SAMPLE_RATIO` | `1.0` | Head sample ratio (0.0–1.0); keep `1.0` for early demos |
| `YAL_REAPER_INTERVAL_S` | `300` | Span reaper tick interval; idle/max-age also via `YAL_RUN_IDLE_TIMEOUT_MS` / `YAL_SPAN_MAX_AGE_MS` |

```bash
docker compose up -d jaeger
# after a Test chat: open http://127.0.0.1:16686 → service yal-supervisor
```

Product `/traces` remains the live session journal timeline (D4); Jaeger is the span tree for the same run.

---

## Verify subagent brief (copy-paste)

```text
VERIFY ONLY — do not edit code.

Scope: LOCAL DEMO NORTH STAR
Guide: .../development/ai-backend-production-grade/LOCAL_DEMO.md

Repo roots:
- AI: .../prime-agent-study-main/prime-agent-study-main
- Outer: .../frontend and .../backend

Score checklist rows 1–7.
For each row: PASS | FAIL | PARTIAL with file/UI/compose evidence.
Mock UI, missing BFF proxy, create session without scope_id, echo-only
claiming "real chat", or Traces nav that redirects to empty mocks = FAIL.

Return:
## Verdict: PASS | FAIL
## Score: N/7
## Per-row evidence
## Gaps blocking demo (P0 first)
## Suggested next Dn / product slice
```

---

## Explicit non-goals for this gate

| Out of local-demo Done | Where it lives |
|------------------------|----------------|
| Multi-replica sticky LB / live bus | D12 HA (local = `replicas=1`) |
| Full Settings BYOK / team chrome | Product honesty under D5/D8 — not required for chat path |
| ClickHouse Trace API waterfall | D13 prod path (demo uses session Traces + Jaeger) |
| Long-running queue/Temporal workers | `long_running_agent_sessions_implementation_plan.md` (after D12) |
| Memory / Prompts product pages | Can stay hidden until D7 product work |

---

## Suggested implement order (after trust spine)

```text
D5  real model (non-echo when keys present)
D6  custom tool FE → publish → session invoke
D9  BFF+FE upload + session scope_id + hydrate
D4  (retro) keep Traces UI required; wire scope on create
D12 live subagent BFF/FE + chips
D13 local Jaeger slice for one Test run
→ LOCAL DEMO verify (this file)
```

You are on **D5** now. Do not skip D5–D8 trust/depth; do not claim demo Done until this checklist is green.

---

*Created 2026-08-30 — productizes the laptop demo as a series gate.*
