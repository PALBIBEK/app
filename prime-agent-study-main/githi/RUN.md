# Running githi

All common commands from the project root:
`/Users/rahulrawatadmin/Desktop/yal/githi`

## 1. One-time setup

```bash
cd /Users/rahulrawatadmin/Desktop/yal/githi

# API key (no spaces around =)
export OPENROUTER_API_KEY='sk-or-v1-...'

# optional model override (default is in config / run.sh)
export GITHI_MODEL='anthropic/claude-haiku-4.5'
# or for run.sh only:
export MODEL='anthropic/claude-sonnet-4'
```

## 2. Interactive REPL (needed for `ask_user_question`)

`ask_user_question` only works in the REPL (not one-shot), because that is where the UI asker is wired.

```bash
# via helper script (reads OPENROUTER_API_KEY from env or ../Github/agent/.env)
./run.sh
VERBOSE=1 ./run.sh

# or directly
.venv/bin/githi --workspace ./githi-workspace -v

# same, via python module
.venv/bin/python -m githi.cli --workspace ./githi-workspace -v
```

Controls:

- `ESC` — interrupt the current run (also cancels owned background subagent
  jobs and running continuables; ESC again escalates)
- `Ctrl-D` — exit
- `Ctrl-C` / `ESC` during `choice>` — abort the ask (`ASK_ABORTED`)

At `choice>`:

- `1` / `2` / … — pick that option
- `0` — then type a custom answer at `your answer>`
- free text — custom answer
- multi-select questions — `1,3` or `1 3`

## 3. One-shot (no interactive ask UI)

```bash
./run.sh "summarize what is in the workspace"

.venv/bin/githi --workspace ./githi-workspace -v "list files here"
```

If the model calls `ask_user_question` here, you get `NO_PROVIDER` (no REPL asker).

## 4. Useful flags

```bash
.venv/bin/githi --help

.venv/bin/githi --workspace ./githi-workspace -v
.venv/bin/githi --workspace ./githi-workspace --model anthropic/claude-haiku-4.5
.venv/bin/githi --workspace ./githi-workspace --max-turns 40
.venv/bin/githi --workspace ./githi-workspace --thinking off
.venv/bin/githi --workspace ./githi-workspace --read-only
.venv/bin/githi --workspace ./githi-workspace --no-memory
```

`./run.sh` also honors:

```bash
MODEL=... WORKSPACE=... MAX_TURNS=20 COMMAND_TIMEOUT=120 THINKING=medium ./run.sh
```

## 5. Prompts that tend to invoke `ask_user_question`

```text
Don't write any files yet. I want a hello CLI in this workspace, but I haven't
chosen the language. Ask me Python or Node, then stop and wait for my answer.
```

```text
Before doing anything else: ask me whether the new script should be hello.py
or hello.js. Do not create files until I answer.
```

```text
Use the ask_user_question tool to ask me Python vs Node for a hello CLI.
Stop after asking.
```

## 5b. Prompts that tend to invoke `subagent` / `subagent_fork`

Both work in REPL and one-shot. Children cannot call `ask_user_question` (`DELEGATED_CALLER`). Depth cap defaults to 3.

**`subagent`** — fresh child (no parent history). Put a full standalone brief in `prompt`.

```text
Use the subagent tool to inspect this workspace for a README and return one
short paragraph. Give the child a self-contained prompt with every path and
instruction it needs. Then summarize its answer.
```

**`subagent_fork`** — child sees completed parent turns (not the current open tool call). `prompt` is only the new task.

```text
We already decided on Python. Use subagent_fork to create hello.py that prints
hello, reusing that choice from our earlier turns.
```

**Background one-shot** — set `run_in_background` true on `subagent` / `subagent_fork`. Tool returns `started background subagent job <id>` immediately; when the child finishes, a settlement notice is injected (`next_turn` mid-run, `follow_up` when idle). Use `job_output` / `job_kill` to poll or cancel.

```text
Start a background subagent to survey large files (run_in_background true),
keep editing README yourself, then summarize the settlement notice or call
job_output with the job id.
```

**Continuable** — `subagent_continuable` returns `started subagent <id>` at inbox accept. Use `send_message` for later turns and `interrupt_agent` to cancel the current turn. After settlement the child is **`ready`** (parked in memory); `send_message` cold-resumes it. Continuable children can call **`report`**. Use **`list_agents`** to recall ids (`running` / `ready`).

```text
Start a continuable subagent to explore the repo. After the settlement
notice, send_message a follow-up to the same subagent id.
```

```text
Start a continuable subagent whose prompt tells it to call report with a
short finding before finishing; summarize the report when it arrives.
```

```text
List your continuable subagents with list_agents, then send_message to
any that are ready.
```

Architecture notes: see `DEEPSEEK-SUBAGENT.md` (section 9).

## 6. Tests

```bash
.venv/bin/python -m pytest tests/test_ask_user.py tests/test_subagent.py tests/test_subagent_fork.py tests/test_subagent_jobs.py tests/test_subagent_continuable.py tests/test_subagent_report.py tests/test_list_agents.py tests/test_repl.py -q
.venv/bin/python -m pytest -q
```

## 7. Do not commit secrets

Never put `OPENROUTER_API_KEY` in a tracked file. Export it in your shell, or keep it in a gitignored env file outside the repo.
