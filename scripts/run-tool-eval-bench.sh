#!/bin/bash
# Full tool-eval-bench run: 88 scenarios x 8 trials, hardmode, seed 42,
# temperature 0, thinking at the production setting (effort low). ~90 minutes.
#
#   ./run-tool-eval-bench.sh
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

echo "=== tool-eval-bench start $(date '+%F %H:%M:%S') (88 scenarios x 8 trials, hardmode, seed 42, temp 0)"
"$TEB" run --base-url "$API" --model "$MODEL_NAME" --backend vllm \
  --temperature 0 --seed 42 --hardmode --trials 8 \
  --label "glm53-flash-nvfp4-3xspark" \
  --output-dir "$RESULTS_DIR/runs" \
  --json-file "$RESULTS_DIR/glm53-flash-nvfp4-3xspark.json" --no-live
echo "=== tool-eval-bench finished rc=$? $(date '+%F %H:%M:%S')"
