#!/usr/bin/env bash
# ctl.sh start|stop|restart|kill <service>|status — one process per compose service.
set -u
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
[ -f "$E2E_RUN_DIR/override.sh" ] && source "$E2E_RUN_DIR/override.sh"
mkdir -p "$E2E_RUN_DIR"/{logs,run,blobs,workspaces}

start_one() {
  local name=$1; shift
  local pidf="$E2E_RUN_DIR/run/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then return; fi
  setsid bash -c "$*" >>"$E2E_RUN_DIR/logs/$name.log" 2>&1 < /dev/null &
  echo $! > "$pidf"
}

start_service() {
  case $1 in
    temporal) start_one temporal "$TEMPORAL_CLI server start-dev --ip 127.0.0.1 --port 7233 --ui-port 8233 --db-filename $E2E_RUN_DIR/temporal.db --log-level warn --dynamic-config-value limit.historyCount.suggestContinueAsNew=120" ;;
    s3)       start_one s3 "$PY -m moto.server -H 127.0.0.1 -p 8333" ;;
    acp)      start_one acp "$PY $E2E_DIR/stack/acp.py" ;;
    relay)    start_one relay "$PY $E2E_DIR/stack/relay.py" ;;
    gateway)  start_one gateway "cd $SVC/model-gateway && env $GW_ENV MODEL_GATEWAY_ROUTES_JSON='$MODEL_GATEWAY_ROUTES_JSON' $GW_PY -m model_gateway" ;;
    memory)   start_one memory "cd $SVC/memory && env $MEM_ENV $MEM_PY -m memory_service" ;;
    api)      start_one api "cd $SVC/supervisor && PORT=8080 HOST=127.0.0.1 $PY -m supervisor.main" ;;
    worker)   start_one worker "cd $SVC/supervisor && $PY -m supervisor.temporal.worker" ;;
    worker2)  start_one worker2 "cd $SVC/supervisor && $PY -m supervisor.temporal.worker" ;;
    *) echo "unknown service: $1" >&2; return 2 ;;
  esac
}

stop_service() {  # $1 name, $2 signal (TERM default)
  local f="$E2E_RUN_DIR/run/$1.pid" sig=${2:-TERM}
  [ -f "$f" ] || return 0
  local pid; pid=$(cat "$f")
  kill -"$sig" -- -"$pid" 2>/dev/null || kill -"$sig" "$pid" 2>/dev/null
  for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  rm -f "$f"
}

ALL="temporal s3 relay acp gateway memory api worker"
case ${1:-} in
  start)   for s in ${2:-$ALL}; do start_service "$s"; done ;;
  stop)    for s in ${2:-worker2 worker api memory gateway acp relay s3 temporal}; do stop_service "$s"; done ;;
  kill)    stop_service "$2" KILL ;;
  restart) stop_service "$2"; start_service "$2" ;;
  status)  for s in $ALL worker2; do
             f="$E2E_RUN_DIR/run/$s.pid"
             if [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; then echo "$s: up"; else echo "$s: down"; fi
           done ;;
  *) echo "usage: ctl.sh start|stop|restart|kill|status [service]"; exit 2 ;;
esac
