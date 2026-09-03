#!/bin/bash
# IFEval only, through the same harness. 541 prompts, ~2 h 10 min.
#
#   ./run-ifeval.sh
#
# CAVEAT: this harness scores IFEval with its own evaluator, and we saw at least
# one prompt marked failed that looked correct to us. Cross-check against the
# official lm-eval `ifeval` task before publishing a number from this run.
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

echo "=== IFEval start $(date '+%F %H:%M:%S')"
"$TEB" run --ifeval-only --base-url "$API" --model "$MODEL_NAME" --backend vllm \
  --temperature 0 --seed 42 --label "glm53-ifeval" \
  --output-dir "$RESULTS_DIR/runs" \
  --json-file "$RESULTS_DIR/glm53-ifeval.json" --no-live
echo "=== IFEval finished rc=$? $(date '+%F %H:%M:%S')"
