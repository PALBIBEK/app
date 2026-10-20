# Agent Platform

Supervisor service, the versioned agent catalog, and shared agent contracts.

```
prime-agent-study-main/
├── services/
│   ├── supervisor/      # the supervisor: API, Temporal worker, sessions, hands  (package: supervisor/)
│   ├── model-gateway/   # the model gateway: routes, keys, wire protocols        (package: model_gateway/)
│   ├── memory/          # the durable memory service                             (package: memory_service/)
│   └── shared/          # code the services share                                (package: shared/)
├── harness/             # the agent catalog: harness versions and compositions
├── contracts/           # protocol_versions.yaml, read at runtime
├── deploy/              # staging and Kubernetes manifests
├── e2e/                 # the end-to-end stack and stages (plus the live real-model stage)
├── scripts/             # CI gates
├── frontend/            # the frontend prototype
└── githi/               # the original githi project
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

See `services/supervisor/PROGRESS.md` for what is built, how it was verified, and its known gaps.
