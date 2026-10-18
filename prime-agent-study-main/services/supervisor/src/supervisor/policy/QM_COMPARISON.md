# Prime Agent vs QM Policy System Comparison

## Feature Comparison

| Feature | Prime (New) | QM | Winner |
|---------|-------------|-----|--------|
| Command Policy | 20+ floor rules, denylist/allowlist | Similar patterns | **Tie** |
| Approval Scopes | once/session/always | once/session/always | **Tie** |
| Distributed Rate Limiting | Postgres UPSERT | Postgres-backed | **Tie** |
| Audit Logging | In-memory + dedup | Full DB persistence | **QM** |
| Cost Estimation | Pre-execution check | Pre-execution + ML pricing | **QM** |
| Approval Recovery | Memory fallback | Full DB persistence | **QM** |
| Stall Detection | Heartbeat-based | Heartbeat + quiet + ML | **QM** |
| Budget Tracking | Decimal, cached | Similar | **Tie** |
| Security Screening | None | ML/proxy classification | **QM** |
| Type Safety | Full enums, protocols | Mixed | **Prime** |
| Code Quality | Fresh, clean architecture | Battle-tested but older | **Prime** (design) / **QM** (proven) |

## QM Still Stronger Because

1. **Security Screening** - QM has ML-based classification for suspicious commands/patterns. We have regex-only.

2. **Production Maturity** - QM's code has been battle-tested. Ours is fresh but unproven.

3. **Full Persistence** - QM persists audit events and recovery context to DB. We fall back to memory.

4. **Richer Stall Detection** - QM combines heartbeat + quiet detection + ML signals.

## Prime Now Stronger In

1. **Type Safety** - Proper enums, Protocol classes, frozen dataclasses with slots
2. **Code Organization** - Clean subsystem separation, clear interfaces
3. **Testability** - Protocol-based DI makes mocking trivial

## To Match QM, Prime Still Needs

1. Security screening (ML/heuristic classification)
2. Full DB persistence for audit events
3. Full DB persistence for approval recovery
4. More sophisticated stall detection signals

## Verdict

QM is ~20% stronger on features, but Prime now has a cleaner foundation to build on. The gap is closeable.
