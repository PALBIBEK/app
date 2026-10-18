# Session at scale — production overlay

**Status:** Index + gap closure for industry session-scale patterns; does not replace D1–D12  
**Wave:** Overlay on D4 (spine), D7 (context/LTM), D12 (warm HA); ops bits for Postgres  
**Depends on:** D1–D4 minimum; D5 metering; D8 HITL ownership; D9/D11 durable deps before multi-tenant multi-replica  
**Related:** Industry synthesis canvas `session-at-scale`; migration crosswalk `../githi-to-services-migration/M0.md`

**Done means (north star):**

> Session identity lives in an append-only durable log (journal), not in worker RAM. Any supervisor can cold-resume. Warm path (SSE live tail, runtime, sandbox, `respond_ui`) has an explicit ownership policy (sticky LB or shared live bus). Hot cache (Redis optional) is never SoT. Context for the next turn is assembled from the store (history + compaction projection + LTM). Outer Test/Sessions read the same log via BFF.

---

## 1. Why this doc exists

D1–D12 harden the AI plane dimension-by-dimension. Industry session-scale material (OpenAI PG, Anthropic Managed Agents, Pixeltable, Google ADK, Redis+PG chat) cuts **across** several Dn:

```text
LB / BFF ──► Supervisor workers
                 │
         Journal SoT (Postgres)     ← D4 (exists)
         Hot live / cache           ← D12 (LiveHub local; Redis missing)
         Context assembly           ← D7 (partial)
         Sandbox cache + files      ← D9 / D10
         HITL / budgets             ← D8
```

Without this overlay, “session at scale” gaps are scattered and easy to miss (especially **PG ops**, **Redis-as-cache-not-ledger**, **public pause/resume**, **ContextLoader seam**).

**There is no D0.** Start at **D1**. Migration history is **M0** in `githi-to-services-migration/`.

---

## 2. Industry checklist → Dn coverage

| # | Session-at-scale need | In D1–D12? | Where | Still missing in practice |
|---|----------------------|------------|-------|---------------------------|
| S1 | Append-only event SoT; worker ≠ agent | **Yes** | D4 | Keep; don’t replace with Redis |
| S2 | Agent config ≠ Environment ≠ Session | **Yes** | D3 pins, D10 sandbox, D4 session | Product picker / dual catalog |
| S3 | Cold resume from shared store | **Yes** | D4, D12 staging | Proven; document as SLO |
| S4 | Warm live tail across replicas | **Yes (policy)** | D12, D4 §4.2 | Sticky LB or shared bus **not shipped** |
| S5 | Hot cache (Redis) for context / pub-sub | **Partial** | D12 optional Redis for limits | No Redis session cache / live bus |
| S6 | Compaction = projection; keep raw log | **Yes** | D7 | Default recall off; turn path incomplete |
| S7 | LTM separate from transcript | **Yes** | D7 | Auth/compose/turn R/W incomplete |
| S8 | Tools as first-class events | **Yes** | D4, D6 | Handlers under composition pins |
| S9 | Credentials out of sandbox | **Yes** | D1, D5, D10 | Scrub universal; defaults |
| S10 | HITL pause without losing prompt | **Yes** | D8 | Sweeper + BFF Approve/Deny |
| S11 | Idle weeks without holding RAM | **Partial** | D4 modes/drain, D12, Google ADK pattern | No product HTTP pause/resume |
| S12 | PG primary + read replicas, pooling | **Weak** | Soft in D2/D12 | **No ops playbook in Dn** — see §4 |
| S13 | Rate limits / workload isolation | **Partial** | D8 tenant limiter; D5 | In-process; not multi-replica |
| S14 | Store-before-fanout (chat rule) | **Yes** | D4 journal then LiveHub | Cross-replica fanout missing |
| S15 | SessionContextLoader seam | **Partial** | D7 MemoryOps / runtime history | Not named/productized; FE ad-hoc |
| S16 | Multi-device channel continuity | **No** | — | Out of Dn scope unless product asks |
| S17 | Separate Session Service deployable | **Deferred** | D12 “don’t split early” | OK co-located until scale forces |

---

## 3. Gaps to close under this overlay (acceptance)

### A. Owned by existing Dn (implement there)

- [ ] **D4:** Journal remains SoT; BFF SSE reads same log; drain gates message/fork; expose `total_cost` for chips  
- [ ] **D4 / product:** HTTP pause/resume **or** hide UI (explicit)  
- [ ] **D7:** Context assembly from journal + compaction projection + optional LTM inject; never delete raw events for “summary only”  
- [ ] **D8:** Approval timeout + sticky `respond_ui` with D12 ownership  
- [ ] **D12:** Choose and enforce HA mode: `replicas=1` \| sticky `session_id` \| shared live bus  
- [ ] **D12:** If Redis introduced — **cache/bus/rate-limit only**, never journal replacement (align M0)  
- [ ] **D9/D11/D5:** Durable workspace/grants/VK before multi-tenant multi-replica  

### B. Missing from D1–D12 today — add here as **ops / S-extra**

| Extra | Requirement | Suggested home |
|-------|-------------|----------------|
| **S-PG** | Staging Postgres: connection pool sizes, statement timeouts, optional read replica for journal **reads**, backup/PITR | New § in this doc + deploy runbook; not a new Dn unless it grows |
| **S-CACHE** | Optional Redis: live pub/sub for SSE envelopes, recent-N message cache, distributed rate limit | Expand D12 acceptance; implement under D12 |
| **S-LOADER** | Single `SessionContextLoader` (or documented equivalent) used by message path — testable, not duplicated in FE | D7 + D4 API contract note |
| **S-SLO** | Document: cold-resume RPO/RTO; warm sticky failure mode | D12 + staging compose comments |

### C. Explicitly out of AI Dn (system / product)

- Multi-device “channel” fan-out for mobile+desktop (unless product requires)  
- Outer FE mocks (Tools/Memory/Budget) — system plane; Dn acceptance *mentions* honesty but code lives in `frontend` / `prime_agent`  
- Firecracker-first sandbox — D10 allows future provider; not session-scale blocker  

---

## 4. Postgres at session scale (S-PG) — minimal playbook

OpenAI’s lesson: **one primary for writes**, scale **reads**, pool connections, rate-limit, isolate noisy workloads. For YAL journal:

| Practice | YAL application |
|----------|-----------------|
| Single writer | One Postgres primary for `events` / `sessions` inserts |
| Read replicas | Optional: list/get/SSE replay from replica; **writes stay primary** |
| Pooling | PgBouncer or SQLAlchemy pool caps on supervisor |
| Cache locking | If Redis hot-cache for recent events: single-flight on miss |
| No new write-heavy tables on journal PG | Large blobs → object store (D9 claim-check); VK spend ledger → gateway store (D5) |
| Rate limits | Edge + D8 tenant limiter (shared store under multi-replica) |

Do **not** shard the event log on day one. Do **not** make Redis the session SoT.

---

## 5. P0 deep-dive gaps — covered by D1–D12?

**Short answer:** Almost all **AI-plane** P0s are already owned by **D1–D12**. There is **no D0**. Outer BFF/FE pieces are **called out in Dn** but implemented in the **system plane** — they are “covered” as acceptance criteria, not as nested-service-only tasks.

| P0 from deep dive | Covered in Dn? | Primary | Notes |
|-------------------|----------------|---------|-------|
| Auth defaults fail-open / spoofable headers | **Yes** | **D1** | Fail-closed staging; claims-only principal |
| Memory HTTP open + IDOR | **Yes** | **D1 + D7** | Auth + tenant on item GET |
| Gateway fail-open empty token | **Yes** | **D1 + D5** | Fail-closed when required |
| Dishonest `/readyz` (no gateway/memory) | **Yes** | **D2** | Upstream probes + sibling `/readyz` |
| Staging memory/none/echo defaults | **Yes** | **D5, D9, D10, D11** | Force postgres/grants + non-none sandbox + real route |
| Composition tools → `TOOL_NOT_IMPLEMENTED` | **Yes** | **D6** | Handlers + githi bridge under pins |
| Approvals BFF / Approve–Deny UI | **Partial** | **D8** (+ system BFF/FE) | Dn requires product path; code in `prime_agent` / frontend |
| Lab tools execute + UUID≠ToolSet | **Partial** | **D6 + D3** (+ BFF) | Publish bridge + Lab invoke proxy |
| Platform status residual gateway/memory | **Yes** | **D2** (+ BFF status) | Aggregate honest `run_allowed` |

### P0 **not** fully inside a single Dn file

| Item | Why |
|------|-----|
| Outer FE mock Tools/Memory/Budget | System UX honesty; referenced across D5–D8, not a nested AI module |
| Dual Lab catalog ↔ YAL composition | D3/D6 bind/publish + BFF harness bind product work |
| S-PG connection pool / replica runbook | **This overlay §4** — was under-specified in D1–D12 |

---

## 6. Implementation order (session scale)

1. **D1 + D2** — trust + honest Run gate (P0).  
2. **D4 BFF complete** — already partly landed; keep SSE = log consumer.  
3. **D6 + D5** — tools under pins + metered non-echo (P0/P1 vertical).  
4. **D8** — sweeper + BFF HITL (P0).  
5. **Force D9/D11 durable defaults** in staging.  
6. **D7** context loader + LTM on turn.  
7. **D12** sticky or live bus **before** warm multi-replica Test.  
8. **S-PG / S-CACHE** as needed under load — after SoT is trusted.

---

## 7. Definition of done (session at scale)

1. Journal is undisputed SoT; Redis (if any) is cache/bus only.  
2. Cold resume SLO documented and tested across supervisor kill.  
3. Warm HA mode chosen and enforced (no silent round-robin).  
4. Context for each turn assembled from store (+ compaction projection), not worker-only RAM.  
5. P0 trust/ready/tools/HITL rows in §5 are closed or explicitly waived with date.  
6. S-PG minimal practices applied in staging compose/runbook.

---

## 8. Related docs

| Doc | Role |
|-----|------|
| [D4.md](./D4.md) | Session spine, SSE, HA deferral |
| [D7.md](./D7.md) | Compaction / LTM / dual log |
| [D8.md](./D8.md) | HITL + budgets |
| [D12.md](./D12.md) | Sticky / live bus / subagents |
| [D1.md](./D1.md)–[D3.md](./D3.md), [D5.md](./D5.md)–[D6.md](./D6.md), [D9.md](./D9.md)–[D11.md](./D11.md) | Trust, ready, bind, gateway, tools, workspace, grants |
| [README.md](./README.md) | Series index + P0 map |
| `../githi-to-services-migration/M0.md` | Phase ↔ Dn; Redis not SoT |

---

*Created 2026-08-30 — pulls session-at-scale industry gaps into ai-backend-production-grade and answers P0 ↔ D1–D12 coverage (no D0).*
