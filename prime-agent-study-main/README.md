# Agent Platform

Supervisor service, the versioned agent catalog, and shared agent contracts.

```
agent-platform/
├── contracts/           # AGENT_CONTRACT + protocol_versions (neither side owns)
├── services/supervisor  # FastAPI supervisor (agent × sandbox axes)
├── harness/             # The agent catalog: githi-v1 / kg-v1 manifests and compositions
├── evals/               # CI evaluation tasks
└── Experiments/         # scratch (gitignored)
```

Two axes in the supervisor:

1. **agent/** — what runs (`AgentRuntime`: githi, the supervisor's own runtime, or echo)
2. **sandbox/** — where it runs (`none` | `local_process` | `local_docker` | `e2b`)

Harness versions declare `requires_sandbox`. Session `modes.py` maps that to
sticky vs. stateless-per-turn.

## Quick start

```bash
docker compose up --build
```

See `services/supervisor/ARCHITECTURE.md` for why the layout is shaped this way.
