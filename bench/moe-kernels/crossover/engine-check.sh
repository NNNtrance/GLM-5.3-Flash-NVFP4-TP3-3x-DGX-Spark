#!/bin/bash
# Engine pre-flight / warm-up helper for the FP4 crossover sweep.
# Read-only except for `warmup`, which sends ONE short completion request.
# Never touches engine containers or systemd units.
set -u
HEAD_HOST=${HEAD_HOST:-head}
HEAD_PORT=${HEAD_PORT:-8000}

api () { ssh -o ConnectTimeout=6 "$HEAD_HOST" "curl -s -m ${2:-10} http://127.0.0.1:${HEAD_PORT}$1"; }

case "${1:-status}" in
  status)
    if ! ssh -o ConnectTimeout=6 "$HEAD_HOST" \
         "curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:${HEAD_PORT}/health" \
         2>/dev/null | grep -q 200; then
      echo "ENGINE: not serving on ${HEAD_HOST}:${HEAD_PORT} (nothing to protect)"
      exit 10
    fi
    M=$(api /metrics 20)
    R=$(echo "$M" | awk '/^vllm:num_requests_running/{print $2}' | head -1)
    W=$(echo "$M" | awk '/^vllm:num_requests_waiting/{print $2}' | head -1)
    echo "ENGINE: serving, running=${R:-?} waiting=${W:-?}"
    case "${R:-1}${W:-1}" in
      0.00.0|00|0.0.0.0) exit 0 ;;
    esac
    # tolerate float formatting
    python3 - "$R" "$W" <<'EOF'
import sys
try:
    r, w = float(sys.argv[1]), float(sys.argv[2])
except Exception:
    sys.exit(2)
sys.exit(0 if (r == 0 and w == 0) else 1)
EOF
    ;;
  warmup)
    MODEL=$(api /v1/models 15 | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
    [ -z "$MODEL" ] && { echo "WARMUP: no model id, skipped"; exit 1; }
    ssh -o ConnectTimeout=6 "$HEAD_HOST" "curl -s -m 180 \
      -H 'Content-Type: application/json' \
      -d '{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Say OK.\"}],\"max_tokens\":8,\"temperature\":0}' \
      http://127.0.0.1:${HEAD_PORT}/v1/chat/completions" \
      | head -c 400
    echo
    echo "WARMUP: sent"
    ;;
  *) echo "usage: $0 {status|warmup}"; exit 64 ;;
esac
