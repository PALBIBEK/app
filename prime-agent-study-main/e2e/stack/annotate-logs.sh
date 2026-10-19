#!/usr/bin/env bash
# In CI, surface the tail of each stack service's log as an annotation (logs are otherwise only in artifacts).
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
for f in "$E2E_RUN_DIR"/logs/*.log; do
  [ -f "$f" ] || continue
  tail=$(grep -vE '^\s*$' "$f" | tail -25 | sed 's/%/%25/g' | awk '{printf "%s%%0A", $0}')
  echo "::error title=$(basename "$f" .log) log::${tail:0:3500}"
done
