#!/bin/bash
# MMLU (or any other lm-eval task) against the engine over its OpenAI-compatible
# API. Nothing is installed on the cluster; this runs on the workstation.
#
#   ./run-lm-eval.sh <label> [limit_per_subtask] [tasks]
#   ./run-lm-eval.sh t10 35 mmlu     57 subtasks x 35 ~= 2,000 questions, ~20 min
#   ./run-lm-eval.sh t10 0  mmlu     limit 0 = full MMLU, 14,042 questions, ~2 h
#
# Requirements (set them in the environment or in scripts/cluster.env):
#   LM_EVAL       path to the lm_eval executable      (default: lm_eval on $PATH)
#   TOKENIZER     local tokenizer directory or HF id  (default: the served model)
#   RESULTS_DIR   where to write results              (default: ../results/lm-eval)
#
# Method: local-completions with echo/logprobs, 0-shot loglikelihood. No
# generation, so no thinking and no temperature are involved -- an lm-eval score
# and a chat score are not the same measurement.
#
# THE TIMEOUT LESSON: our first full-MMLU attempt died after 2 hours with
# asyncio.TimeoutError, and the engine was blameless. lm-eval queues every
# request at once and the aiohttp `total` timeout counts time spent WAITING in
# the connection pool, not just time in flight. With 14,042 questions the tail
# requests time out before they are ever sent. Hence timeout=36000 and
# max_retries=6 below. Do not lower them for a full run.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/lib-cluster.sh"

LABEL=${1:?usage: run-lm-eval.sh <label> [limit] [tasks]}
LIMIT=${2:-35}
TASKS=${3:-mmlu}
LM_EVAL="${LM_EVAL:-lm_eval}"
MODEL_NAME="${MODEL_NAME:-glm-5.3-flash}"
TOKENIZER="${TOKENIZER:-$MODEL_NAME}"
RESULTS_DIR="${RESULTS_DIR:-$D/../results/lm-eval}"

OUT="$RESULTS_DIR/$LABEL"
mkdir -p "$OUT"
curl -s -m 5 "$API/health" -o /dev/null || { echo "engine is not up at $API"; exit 1; }

ARGS="model=$MODEL_NAME,tokenizer=$TOKENIZER,base_url=$API/v1/completions,num_concurrent=8,max_retries=6,tokenized_requests=False,timeout=36000"
LIMARG=""; [ "$LIMIT" != "0" ] && LIMARG="--limit $LIMIT"

echo "=== lm-eval $LABEL | tasks=$TASKS limit=$LIMIT | $(date +%H:%M) ==="
"$LM_EVAL" --model local-completions --model_args "$ARGS" \
  --tasks "$TASKS" --num_fewshot 0 $LIMARG --batch_size 1 \
  --output_path "$OUT" --log_samples 2>&1 \
  | tee "$OUT/log.txt" | grep -vE 'it/s\]|Requesting|^\s*$' | tail -40
echo "=== done $(date +%H:%M) ==="

python3 - "$OUT" <<'PY'
import json,glob,sys,os
fs=sorted(glob.glob(os.path.join(sys.argv[1],"**","results_*.json"),recursive=True))
if not fs:
    print("  no results file"); sys.exit()
r=json.load(open(fs[-1]))["results"]
for k in sorted(r):
    if k in ("mmlu","gsm8k","piqa","hellaswag"):
        v=r[k]
        acc=v.get("acc,none", v.get("acc_norm,none", v.get("exact_match,strict-match")))
        print(f"  {k:12s} acc={acc}")
PY
