#!/usr/bin/env bash
# Bring the end-to-end stack up and wait until the supervisor is ready.
#
# Postgres must already be listening on $PG_HOST:$PG_PORT with databases
# `supervisor` and `memory` owned by $PG_USER (CI uses a service container).
# The service hostnames the wiring uses must resolve to this machine.
set -euo pipefail
here="$(dirname "${BASH_SOURCE[0]}")"
source "$here/env.sh"

for host in model-gateway acp temporal memory-service; do
  getent hosts "$host" >/dev/null || { echo "add '127.0.0.1 $host' to /etc/hosts" >&2; exit 1; }
done

mkdir -p "$E2E_RUN_DIR/harness-versions"
cp "$REPO"/harness/versions/*.yaml "$here"/harness-versions/*.yaml "$E2E_RUN_DIR/harness-versions/"
for f in "$E2E_RUN_DIR"/run/*.pid; do [ -f "$f" ] && ! kill -0 "$(cat "$f")" 2>/dev/null && rm -f "$f"; done

wait_http() {  # url, seconds
  for _ in $(seq 1 "$2"); do curl -sf -m 2 "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "timed out waiting for $1" >&2; return 1
}

"$here/ctl.sh" start "temporal s3 relay acp gateway memory"
wait_http http://127.0.0.1:8333 60
wait_http http://127.0.0.1:8090/v1/healthz 60
for _ in $(seq 1 60); do "$TEMPORAL_CLI" operator cluster health --address 127.0.0.1:7233 >/dev/null 2>&1 && break; sleep 1; done
"$PY" -c "import boto3; boto3.client('s3', endpoint_url='http://127.0.0.1:8333', aws_access_key_id='any', aws_secret_access_key='any', region_name='us-east-1').create_bucket(Bucket='yal-workspace')" 2>/dev/null || true
"$here/ctl.sh" start api
wait_http http://127.0.0.1:8080/readyz 90
"$here/ctl.sh" start worker
sleep 5
"$here/ctl.sh" status | tr '\n' ' '; echo
