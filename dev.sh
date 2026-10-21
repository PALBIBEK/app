#!/usr/bin/env bash
# Run the whole product on this machine: frontend :3000 -> backend :8000 -> supervisor :8080.
#
#   ./dev.sh            start everything (or what is not running yet), seed, print where to log in
#   ./dev.sh stop       stop everything
#   ./dev.sh status     what is up
#   ./dev.sh logs NAME  follow one service's log (postgres temporal s3 gateway supervisor worker backend frontend)
#   ./dev.sh reset      stop, then wipe the three databases and all local state (keeps .env.local)
#
# Needs: Python venvs for the supervisor and the backend, frontend node_modules, the Temporal CLI,
# and Postgres on DEV_PG_PORT (5433), either already running or started with Docker.
# Settings live in .env.local (written on first run from dev.env.example).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDY="$ROOT/prime-agent-study-main"
SUP="$STUDY/services/supervisor"
STATE="$ROOT/.dev"
SERVICES="temporal s3 gateway supervisor worker backend frontend"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# --- settings ---------------------------------------------------------------------------------
if [ ! -f "$ROOT/.env.local" ]; then
  rand() { "${PYTHON:-python3}" -c 'import secrets; print(secrets.token_urlsafe(32))'; }
  sed -e "s|^SERVICE_JWT_SECRET=.*|SERVICE_JWT_SECRET=$(rand)|" \
      -e "s|^BACKEND_SECRET_KEY=.*|BACKEND_SECRET_KEY=$(rand)|" \
      -e "s|^CAPABILITY_SECRET=.*|CAPABILITY_SECRET=$(rand)|" \
      -e "s|^GATEWAY_TOKEN=.*|GATEWAY_TOKEN=$(rand)|" \
      -e "s|^MEMORY_SERVICE_TOKEN=.*|MEMORY_SERVICE_TOKEN=$(rand)|" \
      "$ROOT/dev.env.example" > "$ROOT/.env.local"
  chmod 600 "$ROOT/.env.local"
  say "wrote .env.local (secrets generated; add OPENROUTER_API_KEY there for a real model)"
fi
set -a; source "$ROOT/.env.local"; set +a
export -n OPENROUTER_API_KEY 2>/dev/null || true  # never inherited: only the gateway reads it, from the file

PG_PORT="${DEV_PG_PORT:-5433}" PG_USER="${DEV_PG_USER:-yal}" PG_PASSWORD="${DEV_PG_PASSWORD:-yal}"
PG="postgresql+asyncpg://$PG_USER:$PG_PASSWORD@127.0.0.1:$PG_PORT"
SPY="${SUPERVISOR_PYTHON:-$SUP/.venv/bin/python}"
BPY="${BACKEND_PYTHON:-$ROOT/backend/.venv/bin/python}"
TEMPORAL="${TEMPORAL_CLI:-temporal}"
mkdir -p "$STATE"/{logs,run,blobs,workspaces,harness-versions}

# --- process control --------------------------------------------------------------------------
running() { local f="$STATE/run/$1.pid"; [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; }

start() {  # name, command (runs in its own process group so stop takes its children too)
  running "$1" && return 0
  setsid bash -c "$2" >>"$STATE/logs/$1.log" 2>&1 </dev/null &
  echo $! >"$STATE/run/$1.pid"
}

stop_one() {
  local f="$STATE/run/$1.pid"
  [ -f "$f" ] || return 0
  local pid; pid=$(cat "$f")
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  kill -KILL -- "-$pid" 2>/dev/null || true
  rm -f "$f"
}

wait_http() {  # name, url, seconds
  for _ in $(seq 1 "$3"); do
    curl -sf -m 2 "$2" >/dev/null 2>&1 && return 0
    running "$1" || die "$1 exited; see .dev/logs/$1.log"
    sleep 1
  done
  die "$1 not ready after $3 s ($2); see .dev/logs/$1.log"
}

# --- the parts --------------------------------------------------------------------------------
pg_up() { "$SPY" - "$PG/postgres" <<'EOF' 2>/dev/null
import asyncio, sys, asyncpg
async def main():
    conn = await asyncpg.connect(sys.argv[1].replace("+asyncpg", ""), timeout=3)
    await conn.close()
asyncio.run(main())
EOF
}

ensure_postgres() {
  if ! pg_up; then
    command -v docker >/dev/null && docker info >/dev/null 2>&1 ||
      die "Postgres is not listening on 127.0.0.1:$PG_PORT and Docker is not running.
Start Docker, or run Postgres 16 on port $PG_PORT with user $PG_USER/$PG_PASSWORD (DEV_PG_* in .env.local)."
    say "starting Postgres in Docker"
    docker compose -f "$STUDY/docker-compose.yml" up -d postgres >>"$STATE/logs/postgres.log" 2>&1
    for _ in $(seq 1 60); do pg_up && break; sleep 1; done
    pg_up || die "Postgres did not come up; see .dev/logs/postgres.log"
  fi
  "$SPY" - "$PG/postgres" <<'EOF'
import asyncio, sys, asyncpg
async def main():
    conn = await asyncpg.connect(sys.argv[1].replace("+asyncpg", ""))
    have = {r["datname"] for r in await conn.fetch("SELECT datname FROM pg_database")}
    for db in ("supervisor", "memory", "prime_agent"):
        if db not in have:
            await conn.execute(f'CREATE DATABASE "{db}"')
    await conn.close()
asyncio.run(main())
EOF
}

ensure_hosts() {
  # Shipped compositions pin the gateway as http://model-gateway:8090 (its compose name).
  getent hosts model-gateway >/dev/null && return 0
  say "the shipped harnesses reach the model gateway as 'model-gateway'; adding it to /etc/hosts (sudo)"
  echo "127.0.0.1 model-gateway" | sudo tee -a /etc/hosts >/dev/null ||
    die "add this line to /etc/hosts, then run ./dev.sh again:  127.0.0.1 model-gateway"
}

check_tools() {
  [ -x "$SPY" ] || die "no supervisor venv: cd $SUP && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  [ -x "$BPY" ] || die "no backend venv: cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  [ -d "$ROOT/frontend/node_modules" ] || die "no frontend packages: cd frontend && npm install"
  command -v "$TEMPORAL" >/dev/null || die "Temporal CLI not found: install it (brew install temporal) or set TEMPORAL_CLI"
}

routes_json() {
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then
    printf '{"echo":{"provider":"echo","upstream_model":"echo","context_window":200000,"max_output_tokens":8192,"input_cost_per_token":0,"output_cost_per_token":0,"supported_openai_params":["max_tokens","tools","tool_choice"]},'
    printf '"free":{"provider":"openrouter","upstream_model":"%s","context_window":%s,"max_output_tokens":8192,' \
      "$OPENROUTER_MODEL" "${OPENROUTER_CONTEXT_WINDOW:-128000}"
    printf '"input_cost_per_token":0,"output_cost_per_token":0,"supported_openai_params":["max_tokens","tools","tool_choice","temperature"]}}'
  fi
}

# Environment of the supervisor API and its worker. No provider key: only the gateway holds one.
supervisor_env() {
  cat <<EOF
PROFILE=dev ENV=development HOST=127.0.0.1
TEMPORAL_ADDRESS=127.0.0.1:7233 TEMPORAL_NAMESPACE=default TEMPORAL_TASK_QUEUE=supervisor-sessions
SERVICE_JWT_SECRET='$SERVICE_JWT_SECRET' AI_SERVICE_JWT_SECRET='$SERVICE_JWT_SECRET'
CAPABILITY_SECRET='$CAPABILITY_SECRET' SUPERVISOR_PUBLIC_URL=http://127.0.0.1:8080
GATEWAY_BASE_URL=http://model-gateway:8090 GATEWAY_TOKEN='$GATEWAY_TOKEN'
MODEL_GATEWAY_URL=http://model-gateway:8090 MODEL_GATEWAY_API_KEY='$GATEWAY_TOKEN' MODEL_GATEWAY_API_KEY_HEADER=x-litellm-api-key
${OPENROUTER_API_KEY:+MODEL_GATEWAY_DEFAULT_ROUTE=free}  # upgrades only the echo placeholder: a model picked in the builder stays
OBJECT_STORAGE=s3 OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:8333 OBJECT_STORAGE_BUCKET=yal-workspace
OBJECT_STORAGE_ACCESS_KEY=dev OBJECT_STORAGE_SECRET_KEY=dev OBJECT_STORAGE_REGION=us-east-1
WORKSPACE_STORE=postgres GRANT_STORE=postgres DATABASE_URL='$PG/supervisor'
YAL_CATALOG_DIR='$STUDY/harness' HARNESS_VERSIONS_DIR='$STATE/harness-versions' YAL_CONTRACTS_DIR='$STUDY/contracts'
YAL_LOAD_COMPOSITION_CATALOG=true YAL_PERSIST_CATALOG=true
BLOB_DIR='$STATE/blobs' YAL_SESSION_WORKSPACES='$STATE/workspaces'
SANDBOX_PROVIDER=${SANDBOX_PROVIDER:-local_process}
MEMORY_SERVICE_URL=http://127.0.0.1:8092 MEMORY_SERVICE_TOKEN='$MEMORY_SERVICE_TOKEN'
MEMORY_DATABASE_URL='$PG/memory' MEMORY_PORT=8092 MEMORY_HOST=127.0.0.1 MEMORY_ALLOW_STATIC_TOKEN=1
OTEL_EXPORTER_OTLP_ENDPOINT=
EOF
}

up() {
  check_tools
  ensure_hosts
  ensure_postgres
  cp "$STUDY"/harness/versions/*.yaml "$STATE/harness-versions/"
  local senv; senv=$(supervisor_env | tr '\n' ' ')

  say "starting Temporal, object storage and the model gateway"
  start temporal "$TEMPORAL server start-dev --ip 127.0.0.1 --port 7233 --ui-port 8233 \
    --db-filename '$STATE/temporal.db' --log-level warn"
  start s3 "'$SPY' -m moto.server -H 127.0.0.1 -p 8333"
  # The gateway is the only process given the provider key.
  start gateway "cd '$SUP' && export OPENROUTER_API_KEY=\"\$(grep -s '^OPENROUTER_API_KEY=' '$ROOT/.env.local' | cut -d= -f2-)\" && exec env PROFILE=dev ENV=development HOST=127.0.0.1 \
    MODEL_GATEWAY_TOKEN='$GATEWAY_TOKEN' MODEL_GATEWAY_API_KEY_HEADER=x-litellm-api-key \
    MODEL_GATEWAY_DEFAULT_ROUTE=echo MODEL_GATEWAY_ROUTES_JSON='$(routes_json)' \
    SERVE_SUPERVISOR_API=0 SERVE_MEMORY_SERVICE=0 '$SPY' -m supervisor.main"
  wait_http s3 http://127.0.0.1:8333 60
  wait_http gateway http://127.0.0.1:8090/v1/healthz 60
  for _ in $(seq 1 60); do "$TEMPORAL" operator cluster health --address 127.0.0.1:7233 >/dev/null 2>&1 && break; sleep 1; done
  "$SPY" -c "import boto3; boto3.client('s3', endpoint_url='http://127.0.0.1:8333', aws_access_key_id='dev', \
aws_secret_access_key='dev', region_name='us-east-1').create_bucket(Bucket='yal-workspace')" >/dev/null 2>&1 || true

  say "starting the supervisor (API :8080, memory :8092) and its worker"
  start supervisor "cd '$SUP' && env -u OPENROUTER_API_KEY $senv PORT=8080 SERVE_MODEL_GATEWAY=0 '$SPY' -m supervisor.main"
  wait_http supervisor http://127.0.0.1:8080/readyz 120
  start worker "cd '$SUP' && env -u OPENROUTER_API_KEY $senv '$SPY' -m supervisor.temporal.worker"

  say "starting the backend (:8000) and the frontend (:3000)"
  start backend "cd '$ROOT/backend' && env -u OPENROUTER_API_KEY ENVIRONMENT=development \
    DATABASE_URL='$PG/prime_agent' SECRET_KEY='$BACKEND_SECRET_KEY' \
    AI_SUPERVISOR_BASE_URL=http://127.0.0.1:8080 \
    AI_SERVICE_JWT_SECRET='$SERVICE_JWT_SECRET' \
    '$BPY' -m uvicorn prime_agent.main:app --host 127.0.0.1 --port 8000"
  start frontend "cd '$ROOT/frontend' && env -u OPENROUTER_API_KEY \
    NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npx next dev -p 3000"
  wait_http backend http://127.0.0.1:8000/health 90
  wait_http frontend http://127.0.0.1:3000 180

  "$BPY" "$ROOT/scripts/dev_seed.py"
  echo
  say "Ready: http://localhost:3000"
  echo "  log in as $DEMO_EMAIL / $DEMO_PASSWORD"
  if [ -n "${OPENROUTER_API_KEY:-}" ]; then echo "  model: $OPENROUTER_MODEL (OpenRouter)"
  else echo "  model: echo (repeats your message; set OPENROUTER_API_KEY in .env.local for a real one)"; fi
  echo "  logs: ./dev.sh logs <name>   stop: ./dev.sh stop"
}

stop_all() { for s in frontend backend worker supervisor gateway s3 temporal; do stop_one "$s"; done; }

case "${1:-up}" in
  up|start) up ;;
  stop) stop_all; say "stopped (Postgres keeps running)" ;;
  status) for s in $SERVICES; do running "$s" && echo "$s: up" || echo "$s: down"; done
          pg_up && echo "postgres: up" || echo "postgres: down" ;;
  logs) [ -n "${2:-}" ] || die "usage: ./dev.sh logs <name>"; tail -n 100 -f "$STATE/logs/$2.log" ;;
  reset)
    stop_all
    if pg_up; then
      "$SPY" - "$PG/postgres" <<'EOF'
import asyncio, sys, asyncpg
async def main():
    conn = await asyncpg.connect(sys.argv[1].replace("+asyncpg", ""))
    for db in ("supervisor", "memory", "prime_agent"):
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    await conn.close()
asyncio.run(main())
EOF
    fi
    rm -rf "$STATE"
    say "reset: databases and .dev/ wiped (.env.local kept)" ;;
  *) die "usage: ./dev.sh [up|stop|status|logs NAME|reset]" ;;
esac
