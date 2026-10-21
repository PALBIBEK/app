#!/usr/bin/env bash
# CI: run one journey; on failure, put its output and the service logs' tails in annotations
# (readable from the checks API without downloading the job log).
set -uo pipefail
journey="$1"
here="$(cd "$(dirname "$0")" && pwd)"
logs="$here/../../.dev/logs"
out=$(mktemp)
node "$here/$journey" 2>&1 | tee "$out"
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  esc() { sed 's/%/%25/g' | awk '{printf "%s%%0A", $0}'; }
  echo "::error title=$journey::$(tail -40 "$out" | esc | cut -c1-6000)"
  for svc in backend supervisor worker frontend; do
    [ -f "$logs/$svc.log" ] && echo "::error title=$journey: $svc log::$(tail -30 "$logs/$svc.log" | esc | cut -c1-4000)"
  done
fi
exit "$status"
