# Sourced by the operator-side scripts in this directory. Not executable.
# Loads scripts/cluster.env if present, otherwise falls back to the
# documentation defaults, then exports NODES (head first) and NODES_REV
# (workers first — the order the engine must be started in).
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${CLUSTER_ENV:-$_LIB_DIR/cluster.env}" ]; then
  # shellcheck disable=SC1090
  . "${CLUSTER_ENV:-$_LIB_DIR/cluster.env}"
fi
HEAD_HOST="${HEAD_HOST:-head}"
WORKER1_HOST="${WORKER1_HOST:-worker-1}"
WORKER2_HOST="${WORKER2_HOST:-worker-2}"
API="${API:-http://192.0.2.10:8000}"
SERVICE="${SERVICE:-harem-motor}"
CONTAINER="${CONTAINER:-harem_glm53_lil}"
ENGINE_ENV_FILE="${ENGINE_ENV_FILE:-glm3x/.env.lil-t10}"
NODES="$HEAD_HOST $WORKER1_HOST $WORKER2_HOST"
# Start order: workers first, head last. Stop order is the reverse.
NODES_REV="$WORKER2_HOST $WORKER1_HOST $HEAD_HOST"
SSH="ssh -o BatchMode=yes"
