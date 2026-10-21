# Agent Platform

Supervisor service, the versioned agent catalog, and shared agent contracts.

```
prime-agent-study-main/
├── services/
│   └── supervisor/      # one package, one image (package: supervisor/)
│       ├── supervisor/gateway/          # the model gateway: routes, keys, wire protocols (:8090)
│       ├── supervisor/memory_service/   # the memory service: long-term memory (:8092)
│       └── supervisor/…                 # the API (:8080), Temporal worker, sessions, hands
├── harness/             # the agent catalog: harness versions and compositions
├── contracts/           # protocol_versions.yaml, read at runtime
├── deploy/              # staging and Kubernetes manifests
├── e2e/                 # the end-to-end stack and stages (plus the live real-model stage)
├── scripts/             # CI gates
├── (frontend and backend live at the repository root: ../frontend, ../backend)
└── githi/               # the original githi project
```

Two axes in the supervisor:

1. **agent/** — what runs (`AgentRuntime`: githi, the supervisor's own runtime, or echo)
2. **sandbox/** — where it runs (`none` | `local_process` | `local_docker` | `e2b`)

Harness versions declare `requires_sandbox`. Session `modes.py` maps that to
sticky vs. stateless-per-turn.

`python -m supervisor.main` serves the API, the gateway and the memory service on
their own ports; `SERVE_SUPERVISOR_API`, `SERVE_MODEL_GATEWAY` and
`SERVE_MEMORY_SERVICE` (default on) pick which, so each can run in its own
container from the same image (as `docker-compose.yml` does).

## Quick start

```bash
docker compose up --build
```

See `services/supervisor/PROGRESS.md` for what is built, how it was verified, and its known gaps.
