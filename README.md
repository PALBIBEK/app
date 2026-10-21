# Prime Agent

Build an agent harness in the browser, chat with it, and approve what it does.

| Folder | What it is | Port |
|---|---|---|
| `frontend/` | Next.js app | 3000 |
| `backend/` | FastAPI: users, sign-in, harness records; talks to the supervisor | 8000 |
| `prime-agent-study-main/services/supervisor/` | Runs agent sessions (API, model gateway, memory service, worker) | 8080, 8090, 8092 |

## Run it locally

```sh
./dev.sh
```

It starts Postgres (in Docker unless one already listens on port 5433), Temporal, the
supervisor and its worker, the backend and the frontend, then creates a demo user with one
ready harness. Open http://localhost:3000 and sign in with the login it prints.

First run needs:

- Python venvs: `cd prime-agent-study-main/services/supervisor && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`,
  and the same in `backend/`
- `cd frontend && npm install`
- the [Temporal CLI](https://docs.temporal.io/cli) (`brew install temporal`)
- `127.0.0.1 model-gateway` in `/etc/hosts` (the script offers to add it)

Settings are in `.env.local`, written on first run with fresh secrets. Without a model key
every harness uses the `echo` model, which repeats your message. For a real model, set
`OPENROUTER_API_KEY` there (only the model gateway process receives it) and run `./dev.sh`
again after `./dev.sh stop`.

Other commands: `./dev.sh status`, `./dev.sh logs backend`, `./dev.sh stop`,
`./dev.sh reset` (wipes the databases and local state).

## A tour

Screenshots from `./dev.sh` with a "code-reviewer" harness (two published versions and an
unpublished change). Regenerate them with `cd frontend && node e2e/tour.mjs` (writes `docs/tour/`).

**Your harnesses.** Every harness you made, whether it is published, its model and tools, and
how many sessions you ran.

![Harness list](docs/tour/02-harnesses.png)

**Build it.** The builder has one tab per part of the harness. *Prompt* holds the persona and the
system prompt sections in order (`{cwd}` becomes the working directory). *Tools* picks the
built-in tools and which ones ask before running. *Model* picks a model route. *Sandbox* says where
commands run and what they may touch. *Memory* and *Policy* set compaction, step limits and approvals.

![Prompt tab](docs/tour/03-builder-prompt.png)

A value the platform would refuse is shown on its own tab, before anything is published:

![Policy error](docs/tour/07-builder-policy-error.png)

**Publish versions.** *Publish* turns the draft into a numbered version that new sessions use.
The side panel lists what changed since the live version and every earlier one.
*Export* and *Import* move a harness between accounts as a file.

![Versions](docs/tour/14-builder-versions.png)

**Chat with it.** *Test* opens a session on the live version. A tool that needs approval waits
for you, with a countdown before it is denied. Messages sent meanwhile queue up. The bar shows
steps, tokens and cost, with *Stop*, *Fork* and *End*.

![Approval](docs/tour/09-chat-approval.png)

Tool calls show their input and output. The workspace above the chat holds the session's files:
view, download, upload or delete them, and share the session by email.

![Tool output](docs/tour/10-chat-tool-output.png)

A refused call says why, in plain words:

![Denied call](docs/tour/11-chat-denied-explained.png)

**Come back later.** *Sessions* lists every conversation. Opening one picks up where it left off,
and *Fork from here* starts a new branch at any message.

![Sessions](docs/tour/12-sessions.png)

**Memory.** What each harness remembers about you, in notebooks per version. Add, edit, forget
or search facts. *History* restores a notebook to how it was after any change.

![Memory](docs/tour/15-memory.png)

## Checks

| What | Command |
|---|---|
| Backend tests | `cd backend && .venv/bin/python -m pytest -q tests` |
| Frontend lint, types, tests | `cd frontend && npx next lint && npx tsc --noEmit && npx vitest run` |
| In the browser (needs `./dev.sh` running) | `cd frontend && npm i --no-save playwright && npx playwright install chromium`, then `node e2e/builder-journey.mjs`, `memory-journey.mjs` and `chat-journey.mjs` |

CI runs all of these on every push, including the browser journeys against a fresh `./dev.sh`.
