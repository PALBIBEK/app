#!/usr/bin/env bash
# Run the end-to-end stages against a running stack (see stack/up.sh). Exits non-zero if any check fails.
#   e2e/run.sh            all stages
#   e2e/run.sh 3 7        only stages 3 and 7
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$here/stack/env.sh"
export PYTHONPATH="$here${PYTHONPATH:+:$PYTHONPATH}"
stages=("$@"); [ ${#stages[@]} -eq 0 ] && stages=(2 3 4 6 7 7q)
failed=()
for s in "${stages[@]}"; do
  echo; echo "=================== stage $s ==================="
  if [ "$s" = "7q" ]; then "$PY" "$here/stages/stage7.py" quotas; else "$PY" "$here/stages/stage$s.py"; fi || failed+=("$s")
done
echo
if [ ${#failed[@]} -eq 0 ]; then echo "e2e: all stages passed"; else echo "e2e: failed stages: ${failed[*]}"; exit 1; fi
