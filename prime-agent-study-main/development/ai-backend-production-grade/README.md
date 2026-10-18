# AI backend — production-grade guides

**Canonical hardening track for the nested AI plane** (supervisor, gateway, memory, workspace, sandbox).  
**Start:** [D0.md](./D0.md) (verification loop) → [D1.md](./D1.md). Migration history: [`../githi-to-services-migration/M0.md`](../githi-to-services-migration/M0.md).
**Laptop end-state:** [LOCAL_DEMO.md](./LOCAL_DEMO.md) — required after D1–D13 (compose → harness → tool → subagent → upload → chat → traces).

| Doc | Topic |
|-----|--------|
| [D0.md](./D0.md) | **Verification loop** — implement → verify subagent → fix → repeat (no MVP) |
| [LOCAL_DEMO.md](./LOCAL_DEMO.md) | **Local demo north star** — BFF+FE laptop checklist (not optional) |
| [D1.md](./D1.md) | Service auth & identity |
| [D2.md](./D2.md) | Health / ready gates (+ local compose) |
| [D3.md](./D3.md) | Catalog & harness bind |
| [D4.md](./D4.md) | Sessions + SSE (+ Traces MVP required) |
| [D5.md](./D5.md) | Model gateway |
| [D6.md](./D6.md) | Tools execute (+ custom tool FE required) |
| [D7.md](./D7.md) | Memory (compaction + LTM) |
| [D8.md](./D8.md) | Approvals + budgets |
| [D9.md](./D9.md) | Workspace dual-SoT (+ BFF/FE upload required) |
| [D10.md](./D10.md) | Sandbox isolation |
| [D11.md](./D11.md) | Grants |
| [D12.md](./D12.md) | Subagents + HA (+ live product subagents required) |
| [D13.md](./D13.md) | Production tracing & observability (OTel) |
| [D14.md](./D14.md) | Real local sandbox image (session create / LOCAL_DEMO #5) |
| [SESSION-AT-SCALE.md](./SESSION-AT-SCALE.md) | Overlay: industry session-scale gaps + P0 ↔ Dn map |
| [agent_platform_production_tracing_implementation_plan.md](./agent_platform_production_tracing_implementation_plan.md) | Detailed 35-phase tracing plan (owned by D13) |
| [long_running_agent_sessions_implementation_plan.md](./long_running_agent_sessions_implementation_plan.md) | Post-D12 durable workers (not a local-demo gate) |

## Local demo north star

> After D1–D13: `docker compose up` → create/bind harness → custom tool → subagent → upload workspace file → Test chat that can `read` it → session Traces (+ Jaeger). Details and verify brief: **[LOCAL_DEMO.md](./LOCAL_DEMO.md)**.

Dn PASS ≠ demo Done. Mock Tools/Subagents/upload or session create without `scope_id` fails the gate.

## Waves

0. **D0** — verification loop required for every Dn  
1. **D1 → D4** — trust, ready, bind, session spine (blocking)  
2. **D5 → D8** — gateway, tools, memory, HITL/budget (depth); **D13** can harden after D4  
3. **D9 → D12** — durable files, isolation, grants, warm HA / children  
4. **D13** — Collector/Jaeger (local demo slice required) → Trace API/UI → ClickHouse (prod path)  
5. **LOCAL DEMO gate** — [LOCAL_DEMO.md](./LOCAL_DEMO.md) checklist PASS (then optional long-running plan)  

## P0 deep-dive → Dn (summary)

| P0 | Dn |
|----|-----|
| Auth fail-open / spoof headers | D1 |
| Memory open + IDOR | D1 + D7 |
| Gateway fail-open | D1 + D5 |
| False ready (no gateway/memory probe) | D2 |
| Staging memory/none/echo defaults | D5, D9–D11, D10 |
| Composition tools not implemented | D6 |
| Approvals UI/BFF | D8 (+ system BFF/FE) |
| Lab tool execute / UUID bridge | D6 + D3 (+ BFF) |

Full detail + session-scale extras (PG pool, Redis-as-cache): [SESSION-AT-SCALE.md](./SESSION-AT-SCALE.md) §5–§4.  
Tracing status: [D13.md](./D13.md).
**Laptop demo gate:** [LOCAL_DEMO.md](./LOCAL_DEMO.md).
