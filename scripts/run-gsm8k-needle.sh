#!/bin/bash
# GSM8K (200 questions, 8-shot CoT, ~8 min) and then the needle-in-a-haystack
# run, but only if the engine is still answering after GSM8K.
#
#   ./run-gsm8k-needle.sh
#
# The needle part here uses the harness default timeout. For a real long-context
# run use run-needle.sh instead, which sets --timeout 3600.
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
B=("$TEB" run --base-url "$API" --model "$MODEL_NAME" --backend vllm
   --temperature 0 --seed 42 --output-dir "$RESULTS_DIR/runs" --no-live)

echo "=== GSM8K start $(date '+%F %H:%M:%S')"
"${B[@]}" --gsm8k-only --label glm53-gsm8k --json-file "$RESULTS_DIR/glm53-gsm8k.json"
echo "=== GSM8K finished rc=$? $(date '+%F %H:%M:%S')"

curl -s -m 5 "$API/health" -o /dev/null || { echo "=== engine down, needle skipped"; exit 1; }

echo "=== NEEDLE start $(date '+%F %H:%M:%S')"
"${B[@]}" --needle-only --label glm53-needle --json-file "$RESULTS_DIR/glm53-needle.json"
echo "=== NEEDLE finished rc=$? $(date '+%F %H:%M:%S')"
