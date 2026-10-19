# Environment for the end-to-end stack: docker-compose's wiring, as plain processes.
# Every path derives from this file's location; override any of them from the caller.
E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export E2E_DIR
export REPO="${REPO:-$(cd "$E2E_DIR/.." && pwd)}"
export SVC="$REPO/services"
export E2E_RUN_DIR="${E2E_RUN_DIR:-$E2E_DIR/.run}"
export PY="${E2E_PYTHON:-$SVC/supervisor/.venv/bin/python}"
export GW_PY="${E2E_GATEWAY_PYTHON:-$SVC/model-gateway/.venv/bin/python}"
export MEM_PY="${E2E_MEMORY_PYTHON:-$SVC/memory/.venv/bin/python}"
export TEMPORAL_CLI="${TEMPORAL_CLI:-temporal}"
export PG_HOST="${PG_HOST:-127.0.0.1}" PG_PORT="${PG_PORT:-5433}" PG_USER="${PG_USER:-yal}" PG_PASSWORD="${PG_PASSWORD:-yal}"

# --- model gateway ---------------------------------------------------------
export GW_ENV="PROFILE=dev ENV=development PORT=8090 MODEL_GATEWAY_TOKEN=dev-local-gateway-token \
MODEL_GATEWAY_API_KEY_HEADER=x-litellm-api-key MODEL_GATEWAY_DEFAULT_ROUTE=echo \
OPENAI_API_KEY=standin OPENAI_BASE_URL=http://127.0.0.1:9100/v1 \
ANTHROPIC_API_KEY=standin ANTHROPIC_BASE_URL=http://127.0.0.1:9100"
# Test routes: the echo model behind each wire protocol (via the recording relay), with limits and prices.
export MODEL_GATEWAY_ROUTES_JSON='{
 "echo": {"provider":"echo","upstream_model":"echo","context_window":200000,"max_output_tokens":8192,"input_cost_per_token":0.000001,"output_cost_per_token":0.000002},
 "standin-chat": {"provider":"openai","upstream_model":"echo","providers":["vllm"],"context_window":200000,"max_output_tokens":8192,"input_cost_per_token":0.000001,"output_cost_per_token":0.000002,"supported_openai_params":["max_completion_tokens"]},
 "standin-responses": {"provider":"openai","upstream_model":"echo","providers":["openai"],"context_window":200000,"max_output_tokens":8192,"input_cost_per_token":0.0000025,"output_cost_per_token":0.00001,"supports_reasoning":true,"supported_openai_params":["reasoning_effort","max_completion_tokens"]},
 "standin-claude": {"provider":"anthropic","upstream_model":"echo","providers":["anthropic"],"context_window":200000,"max_output_tokens":8192,"input_cost_per_token":0.000003,"output_cost_per_token":0.000015,"supports_reasoning":true,"supports_vision":true},
 "tiny-window": {"provider":"openai","upstream_model":"echo","providers":["vllm"],"context_window":3000,"max_output_tokens":500,"input_cost_per_token":0.000001,"output_cost_per_token":0.000002}
}'

# --- supervisor (API + worker share this) ----------------------------------
export TEMPORAL_ADDRESS=127.0.0.1:7233 TEMPORAL_NAMESPACE=default TEMPORAL_TASK_QUEUE=supervisor-sessions
export PROFILE=dev ENV=development
export SERVICE_JWT_SECRET='dev-local-service-jwt-secret-32b!!' AI_SERVICE_JWT_SECRET='dev-local-service-jwt-secret-32b!!'
export GATEWAY_BASE_URL=http://model-gateway:8090 GATEWAY_TOKEN=dev-local-gateway-token
export MODEL_GATEWAY_URL=http://model-gateway:8090 MODEL_GATEWAY_API_KEY=dev-local-gateway-token MODEL_GATEWAY_API_KEY_HEADER=x-litellm-api-key
export SUPERVISOR_PUBLIC_URL=http://127.0.0.1:8080 CAPABILITY_SECRET='dev-local-capability-secret-32bytes!'
export OBJECT_STORAGE=s3 OBJECT_STORAGE_ENDPOINT=http://127.0.0.1:8333 OBJECT_STORAGE_BUCKET=yal-workspace \
  OBJECT_STORAGE_ACCESS_KEY=any OBJECT_STORAGE_SECRET_KEY=any OBJECT_STORAGE_REGION=us-east-1
export WORKSPACE_STORE=postgres GRANT_STORE=postgres
export DATABASE_URL="postgresql+asyncpg://$PG_USER:$PG_PASSWORD@$PG_HOST:$PG_PORT/supervisor"
export YAL_CATALOG_DIR=$REPO/harness HARNESS_VERSIONS_DIR=$E2E_RUN_DIR/harness-versions YAL_CONTRACTS_DIR=$REPO/contracts
export YAL_LOAD_COMPOSITION_CATALOG=true YAL_PERSIST_CATALOG=true
export BLOB_DIR=$E2E_RUN_DIR/blobs YAL_SESSION_WORKSPACES=$E2E_RUN_DIR/workspaces
export SANDBOX_PROVIDER=local_process   # Docker and e2b sandboxes are covered by the compose job
export MEMORY_SERVICE_URL=http://memory-service:8092 MEMORY_SERVICE_TOKEN=dev-local-memory-token
export OTEL_EXPORTER_OTLP_ENDPOINT= YAL_REAPER_INTERVAL_S=300
export ALLOW_LAB_ECHO=0   # production subagent default: nested sessions through the gateway

# --- memory service ----------------------------------------------------------
export MEM_ENV="MEMORY_DATABASE_URL=postgresql+asyncpg://$PG_USER:$PG_PASSWORD@$PG_HOST:$PG_PORT/memory MEMORY_PORT=8092 \
MEMORY_HOST=127.0.0.1 MEMORY_SERVICE_TOKEN=dev-local-memory-token MEMORY_ALLOW_STATIC_TOKEN=1 ENV=development \
SERVICE_JWT_SECRET=dev-local-service-jwt-secret-32b!!"

# --- test helpers ------------------------------------------------------------
export RELAY_LOG=$E2E_RUN_DIR/relay.jsonl ACP_LOG=$E2E_RUN_DIR/acp.jsonl
export E2E_CTL="$E2E_DIR/stack/ctl.sh"
