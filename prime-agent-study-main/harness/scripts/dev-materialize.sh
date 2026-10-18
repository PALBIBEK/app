#!/usr/bin/env bash
# Materialize .yal/ for local harness development (no running supervisor required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLATFORM="$(cd "$ROOT/.." && pwd)"
WORKSPACE="${1:-$ROOT/.dev-workspace}"
COMPOSITION="${2:-coding-v1}"

export PYTHONPATH="$PLATFORM/services/supervisor/src${PYTHONPATH:+:$PYTHONPATH}"
python3 "$PLATFORM/services/supervisor/scripts/materialize_harness_dev.py" \
  --catalog-dir "$ROOT" \
  --composition "$COMPOSITION" \
  --workspace "$WORKSPACE"

echo "materialized $WORKSPACE/.yal (composition=$COMPOSITION)"
