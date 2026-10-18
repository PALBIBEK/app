#!/bin/sh
# Create the namespace sessions run in, with a history retention period. Idempotent.
set -eu
ADDRESS="${TEMPORAL_ADDRESS:-temporal:7233}"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"
RETENTION="${TEMPORAL_RETENTION:-7d}"
ATTEMPTS="${TEMPORAL_READY_ATTEMPTS:-60}"

i=0
until temporal operator cluster health --address "$ADDRESS" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge "$ATTEMPTS" ]; then echo "temporal not healthy after $ATTEMPTS attempts"; exit 1; fi
  echo "waiting for temporal ($i/$ATTEMPTS)..."; sleep 2
done

if temporal operator namespace describe -n "$NAMESPACE" --address "$ADDRESS" >/dev/null 2>&1; then
  echo "namespace $NAMESPACE exists"
else
  temporal operator namespace create -n "$NAMESPACE" --retention "$RETENTION" --address "$ADDRESS"
  echo "namespace $NAMESPACE created (retention $RETENTION)"
fi
