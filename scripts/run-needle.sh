#!/bin/bash
# Needle-in-a-haystack on its own, with --timeout 3600. ~88 minutes.
#
#   ./run-needle.sh
#
# THE TIMEOUT LESSON: our first needle run was meaningless because the harness
# default timeout is 120 seconds. A 250,000-token prefill alone takes longer
# than that on this cluster (prefill runs at roughly 1,560 tok/s), so every
# large-context probe "failed" without the engine ever being at fault. A 1M
# request needs about 11 minutes of prefill; give the harness an hour.
#
# tool-eval-bench is a THIRD-PARTY evaluation harness, not part of this recipe
# and not redistributed here. Install it yourself and point TOOL_EVAL_BENCH at
# the executable (or put it on $PATH). Check its own licence before use.
#
# Config (environment or scripts/cluster.env):
#   API                the engine endpoint          (default http://192.0.2.10:8000)
#   TOOL_EVAL_BENCH    path to the executable       (default tool-eval-bench on $PATH)
#   RESULTS_DIR        where reports are written    (default ../results/tool-eval-bench)
#   MODEL_NAME         served model name            (default glm-5.3-flash)
set -u
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/lib-cluster.sh"
TEB="${TOOL_EVAL_BENCH:-tool-eval-bench}"
MODEL_NAME="${MODEL_NAME:-glm-5.3-flash}"
RESULTS_DIR="${RESULTS_DIR:-$D/../results/tool-eval-bench}"
mkdir -p "$RESULTS_DIR/runs"

echo "=== NEEDLE start $(date '+%F %H:%M:%S') (timeout 3600)"
"$TEB" run --needle-only --base-url "$API" --model "$MODEL_NAME" --backend vllm \
  --temperature 0 --seed 42 --timeout 3600 --label glm53-needle \
  --output-dir "$RESULTS_DIR/runs" \
  --json-file "$RESULTS_DIR/glm53-needle.json" --no-live
echo "=== NEEDLE finished rc=$? $(date '+%F %H:%M:%S')"
