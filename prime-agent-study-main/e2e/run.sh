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
  out="$E2E_RUN_DIR/stage$s.out"; mkdir -p "$E2E_RUN_DIR"
  if [ "$s" = "7q" ]; then cmd=("$PY" "$here/stages/stage7.py" quotas); else cmd=("$PY" "$here/stages/stage$s.py"); fi
  if ! "${cmd[@]}" 2>&1 | tee "$out"; then
    failed+=("$s")
    # In CI each failed check becomes an annotation (job logs are not always at hand).
    if [ -n "${GITHUB_ACTIONS:-}" ]; then
      grep -E '^(FAIL |Traceback|[A-Za-z]+Error)' "$out" | head -20 | sed "s/^/::error title=e2e stage $s::/"
    fi
  fi
done
echo
if [ ${#failed[@]} -eq 0 ]; then echo "e2e: all stages passed"; else echo "e2e: failed stages: ${failed[*]}"; exit 1; fi
