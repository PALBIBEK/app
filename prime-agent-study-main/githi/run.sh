#!/usr/bin/env bash
# Start githi with the environment already set up.
#
# Full command cheat sheet: see RUN.md in this directory.
#
#   ./run.sh                    interactive session (ESC interrupts, Ctrl-D exits)
#   ./run.sh "your question"    one-shot, prints the answer and exits
#   VERBOSE=1 ./run.sh          same with -v (tool trace)
#
# Everything below is convenience: the API key, a free model, and a workspace
# that persists between sessions so the agent can keep files it has cloned.

set -euo pipefail
cd "$(dirname "$0")"

# Captured before sourcing the env file. That file belongs to an older agent
# and sets its own MODEL (an OpenRouter id that this client rejects with a
# 400), so letting it win here silently breaks every run with an unhelpful
# "[error] 1 turns | 0 tokens". Only a MODEL the *caller* set should override
# the default below.
CALLER_MODEL="${MODEL:-}"

# The key lives outside this repo so it is never committed.
ENV_FILE="../Github/agent/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "No OPENROUTER_API_KEY found." >&2
  echo "Either put it in $ENV_FILE or run: export OPENROUTER_API_KEY=sk-..." >&2
  exit 2
fi

# A free model that supports tool calling. Most free models on OpenRouter do
# not, and the agent is useless without it. Override with: MODEL=... ./run.sh
MODEL="${CALLER_MODEL:-nvidia/nemotron-3-super-120b-a12b:free}"

# The sandbox root. Every file the agent reads or writes is confined here, and
# it persists across runs so a cloned repo survives into the next session.
WORKSPACE="${WORKSPACE:-$PWD/githi-workspace}"
mkdir -p "$WORKSPACE"

args=(
  --model "$MODEL"
  --workspace "$WORKSPACE"
  --max-turns "${MAX_TURNS:-20}"
  --command-timeout "${COMMAND_TIMEOUT:-120}"
  --thinking "${THINKING:-medium}"
)
if [[ "${VERBOSE:-}" == "1" || "${VERBOSE:-}" == "true" ]]; then
  args+=(-v)
fi

exec .venv/bin/githi "${args[@]}" "$@"
