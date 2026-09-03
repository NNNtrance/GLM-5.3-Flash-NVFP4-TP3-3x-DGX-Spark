#!/usr/bin/env bash
# Launch ONE rank of local-inference-lab/GLM-5.3-Flash-NVFP4 on a DGX Spark
# (GB10), Local Inference Lab stack, image harem/glm53-lil:t10.
# TP=3 + expert parallelism + DFlash2 k=7, three nodes, no switch.
#
#   ./start-lil.sh 1      on worker-1  (worker)
#   ./start-lil.sh 2      on worker-2  (worker)
#   ./start-lil.sh 0      on head      (rank 0, LAST)
#
# Workers first, then rank 0. Tear down ALL ranks before relaunching any — a
# single-node restart kills the peer's fabric port (see docs/04-autostart.md).
# The caller drops caches before a cold start; this script does not.
#
#   DRY_RUN=1 ./start-lil.sh 0        print the docker command, run nothing
#   ENV_FILE=$HOME/glm3x/.env.lil-t10 ./start-lil.sh 0
#
# Under systemd the rank is not passed on the command line: NODE_RANK comes
# from the env file, which is why every node needs its own (scripts/env.example).
#
# Serving flags are what `lil render GLM-5.3-Flash-NVFP4` emits, with our
# deviations marked in place. Written by us for this recipe; use freely
# (Apache-2.0), a credit is appreciated.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/../.env.lil-t10}"
test -f "$ENV_FILE" || { echo "ENV_FILE not found: $ENV_FILE" >&2; exit 2; }
echo "ENV_FILE=$ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

NODE_RANK="${1:-${NODE_RANK:?set NODE_RANK or pass rank}}"
IMAGE="${IMAGE:-harem/glm53-lil:t10}"
NAME="${NAME:-harem_glm53_lil}"
MODEL_HOST_PATH="${MODEL_HOST_PATH:-/var/tmp/glm-5.3-flash-lil-nvfp4}"
MODEL_PATH="${MODEL_PATH:-/models/glm-5.3-flash-lil-nvfp4}"
DRAFT_HOST_PATH="${DRAFT_HOST_PATH:-/var/tmp/dflash2-draft}"
DRAFT_PATH="${DRAFT_PATH:-/models/dflash2-draft}"
CACHE_HOST_PATH="${CACHE_HOST_PATH:-/var/tmp/glm53-lil-cache}"
PORT="${PORT:-8000}"
NNODES="${NNODES:-3}"
TP_SIZE="${TP_SIZE:-3}"
PP_SIZE="${PP_SIZE:-1}"
MASTER_ADDR="${MASTER_ADDR:?}"
MASTER_PORT="${MASTER_PORT:-29521}"
HOST_IP="${HOST_IP:?}"
GLOO_IFACE="${GLOO_IFACE:-enP7s7}"
TRANSPORT="${TRANSPORT:-mesh}"
NCCL_MESH_PLUGIN_DIR="${NCCL_MESH_PLUGIN_DIR:-$HOME/glm3x/nccl-mesh}"

# --- FIXED, not tunable ----------------------------------------------------
# Operator decision: DFlash2 speculative depth is 7. The lab manifest agrees
# for this checkpoint (lil.yaml speculators.dflash.tokens: 7). Not swept, not
# read from the environment.
DFLASH_NUM_SPEC=7

# --- preflight: every mount source must EXIST -------------------------------
# A bind mount whose source is missing makes docker create an empty DIRECTORY
# there and the engine then fails somewhere that does not name the cause.
test -f "$MODEL_HOST_PATH/config.json" || { echo "no model at $MODEL_HOST_PATH" >&2; exit 2; }
test -f "$MODEL_HOST_PATH/model.safetensors.index.json" \
  || { echo "no weight index at $MODEL_HOST_PATH" >&2; exit 2; }
if [ "${SPEC_METHOD:-dflash}" = "dflash" ]; then
  test -f "$DRAFT_HOST_PATH/config.json" || { echo "no drafter at $DRAFT_HOST_PATH" >&2; exit 2; }
  test -f "$DRAFT_HOST_PATH/model.safetensors" \
    || { echo "no drafter weights at $DRAFT_HOST_PATH" >&2; exit 2; }
fi
mkdir -p "$CACHE_HOST_PATH/triton" "$CACHE_HOST_PATH/b12x" "$CACHE_HOST_PATH/cutedsl" \
         "$CACHE_HOST_PATH/huggingface"

docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "image $IMAGE not on this node" >&2; exit 2; }

# --- addresses --------------------------------------------------------------
# MASTER_ADDR and VLLM_HOST_IP must be MANAGEMENT addresses. A fabric address
# hangs the rendezvous silently: the fabric is a set of pairwise
# point-to-point subnets, so a worker has no route to the head over a cable it
# is not attached to. Set FABRIC_PREFIX to the leading octets of your fabric
# addressing (for example "198.51.100.") to have that mistake refused here.
FABRIC_PREFIX="${FABRIC_PREFIX:-}"
if [ -n "$FABRIC_PREFIX" ]; then
  for _addr_name in MASTER_ADDR HOST_IP; do
    case "${!_addr_name}" in
      "$FABRIC_PREFIX"*) echo "$_addr_name=${!_addr_name} is a fabric address; use the management IP" >&2
                         exit 2 ;;
    esac
  done
fi

# --- TP=3 shape -------------------------------------------------------------
# The TARGET model is padded 64 -> 66 heads through --hf-overrides and the
# HAREM-TP3-LIL patches baked into the image. Its config.json on disk is NOT touched:
# the checkpoint ships SHA256SUMS and must stay verifiable.
#
# The DRAFTER is different: SpeculativeConfig builds a full ModelConfig from
# the drafter checkpoint's own config.json, which --hf-overrides does not
# reach. Our three nodes carry a drafter whose config.json was padded to 36/9
# GQA (config.json.orig next to it holds the stock 32/8), and at
# draft_tensor_parallel_size=1 that is still correct because the image's
# HAREM-TP3-LIL pad in model_executor/parameter.py grows the stored 32/8
# tensors to 36/9 with zeros. Re-pad only when the draft TP itself needs it —
# which at DFLASH_DRAFT_TP=1 it never does, so the branch below is unreachable
# in the production configuration and PAD_SCRIPT is not shipped with this
# recipe. Point PAD_SCRIPT at your own padder if you change the draft TP.
DFLASH_DRAFT_TP="${DFLASH_DRAFT_TP:-1}"
if [ "${SPEC_METHOD:-dflash}" = "dflash" ] && [ $((32 % DFLASH_DRAFT_TP)) -ne 0 ]; then
  PAD_SCRIPT="${PAD_SCRIPT:?set PAD_SCRIPT: the drafter GQA padder is not shipped with this recipe}"
  test -f "$PAD_SCRIPT" || { echo "pad script missing: $PAD_SCRIPT" >&2; exit 2; }
  test -w "$DRAFT_HOST_PATH/config.json" || {
    echo "drafter config not writable — the GQA 32/8 -> 36/9 pad needs to edit it" >&2
    exit 2; }
  python3 "$PAD_SCRIPT" "$DRAFT_HOST_PATH/config.json" --tp "$DFLASH_DRAFT_TP"
fi

HEADLESS=""
[ "$NODE_RANK" != "0" ] && HEADLESS="--headless"

# No KV pin. lil leaves --kv-cache-memory-bytes absent and lets vLLM profile;
# the lab's own two-node spark script pins 10G. We follow lil: pinning buys
# pool but removes the activation safety margin.
KV_MEM_ARG=()
[ -n "${KV_CACHE_MEMORY:-}" ] && KV_MEM_ARG=(--kv-cache-memory-bytes "$KV_CACHE_MEMORY")

# DFlash2 with OUR BF16 drafter, not the lab's MXFP8 one (lil would emit
# "model":"local-inference-lab/GLM-5.3-Flash-DFlash2-MXFP8"). All four keys are
# real SpeculativeConfig fields in this fork (vllm/config/speculative.py:394,
# 406, 411, 415); draft_tensor_parallel_size must be 1 or the target TP, any
# other value raises (speculative.py:1683-1690).
SPEC_ARG=()
if [ "${SPEC_METHOD:-dflash}" = "dflash" ]; then
  SPEC_ARG=(--speculative-config "{\"method\":\"dflash\",\"model\":\"${DRAFT_PATH}\",\"num_speculative_tokens\":${DFLASH_NUM_SPEC},${DRAFT_ATTN_BACKEND:+\"attention_backend\":\"$DRAFT_ATTN_BACKEND\",}\"kv_cache_dtype\":\"${DRAFT_KV_DTYPE:-auto}\",\"draft_tensor_parallel_size\":${DFLASH_DRAFT_TP},\"draft_load_config\":{\"load_format\":\"${DRAFT_LOAD_FORMAT:-safetensors}\"}}")
fi

# --- MoE backend vs expert parallelism --------------------------------------
# 288 routed experts shard cleanly across 3 ranks (288 % 3 = 0); the 2048-wide
# routed intermediate does not (2048 % 3 = 2). EP is therefore MANDATORY at
# TP=3 — with EP on, the routed width is not divided at all.
#
# The lab's own MoE backend cannot do EP:
#   fused_moe/b12x.py:486-495  _supports_parallel_config  needs not use_ep
#   fused_moe/b12x.py:504-505  supports_expert_map() -> False
#   fused_moe/b12x.py:764-765  raise "b12x TP MoE does not support expert maps"
# flashinfer_b12x refuses EP as well (experts/flashinfer_b12x_moe.py:199-202),
# and cutlass needs ep_size == 1 (experts/cutlass_moe.py:736-739). marlin is
# the only SM12x backend here that accepts EP (experts/marlin_moe.py:642-648
# rejects only the FI-NVL two-sided kernels) — which is also what our NVFP4
# production line runs.
# Known, unmeasured cost: marlin is WEIGHT-ONLY. oracle/nvfp4.py:448-467 forces a13_scale and
# a2_scale to None and oracle/nvfp4.py:539-550 hands it a w4a16 quant config,
# so this checkpoint's W4A4 activation scales are silently dropped and the MoE
# runs FP4-weights / BF16-activations. Quality and speed cost NOT measured here.
if [ "$TP_SIZE" -eq 3 ] && [ "${ENABLE_EP:-1}" != "1" ]; then
  echo "TP=3 with ENABLE_EP=0 cannot load this checkpoint (routed 2048 % 3). Refusing." >&2
  exit 2
fi
MOE_BACKEND="${MOE_BACKEND:-marlin}"
if [ "${ENABLE_EP:-1}" = "1" ] && [ "${ALLOW_B12X_EP:-0}" != "1" ]; then   # the b12x MoE+EP patch exists but produces broken output; keep this refusal on
  case "$MOE_BACKEND" in
    b12x|flashinfer_b12x|cutlass)
      echo "ENABLE_EP=1 with MOE_BACKEND=$MOE_BACKEND cannot work (that backend refuses expert maps). Refusing." >&2
      exit 2 ;;
  esac
fi
EP_ARG=()
[ "${ENABLE_EP:-1}" = "1" ] && EP_ARG=(--enable-expert-parallel)

# Production runs with graphs (ENFORCE_EAGER=0) plus the three AOT flags.
# Under --enforce-eager there is no torch.compile to feed, so the AOT flags
# must be flipped together with ENFORCE_EAGER. Measured trade: graphs give
# +22% at concurrency 1 and cost 12% of the KV pool.
EAGER_ARG=()
[ "${ENFORCE_EAGER:-1}" = "1" ] && EAGER_ARG=(--enforce-eager)

# 66 heads. moe_intermediate_size is deliberately NOT overridden: the routed
# experts stay 2048 and are distributed by EP; only the BF16 shared expert is
# padded 2048 -> 2112, inside the model, by the HAREM-TP3-LIL patch.
HF_OVERRIDE_ARG=()
if [ -z "${HF_OVERRIDES:-}" ] && [ "$TP_SIZE" -gt 1 ] && [ $((64 % TP_SIZE)) -ne 0 ]; then
  HF_OVERRIDES='{"num_attention_heads":66,"num_key_value_heads":66,"linear_num_heads":66,"text_config":{"num_attention_heads":66,"num_key_value_heads":66,"linear_num_heads":66}}'
fi
[ -n "${HF_OVERRIDES:-}" ] && HF_OVERRIDE_ARG=(--hf-overrides "$HF_OVERRIDES")

# Vision tower is 16 heads; 16 % 3 != 0, so data-parallel the encoder. The lab
# manifest asks for this unconditionally (mm_encoder_tp_mode: data).
MM_ARG=(--mm-encoder-tp-mode "${MM_ENCODER_TP_MODE:-data}"
        --mm-processor-cache-gb "${MM_PROCESSOR_CACHE_GB:-0}")
if [ "${LANGUAGE_MODEL_ONLY:-0}" = "1" ]; then
  MM_ARG+=(--language-model-only)
else
  [ "${SKIP_MM_PROFILING:-1}" = "1" ] && MM_ARG+=(--skip-mm-profiling)
  MM_ARG+=(--limit-mm-per-prompt "{\"image\": ${MM_IMAGE_LIMIT:-4}, \"video\": 1}")
fi

# The GLM-5.3-Flash chat template shipped with this checkpoint expects
# reasoning_effort and falls back to "max" when it is absent; the older one
# reads enable_thinking. Send both keys — each template reads the one it knows.
# There is no OFF switch: enable_thinking=false only removes the filter and
# leaks the reasoning into the answer. Never set it (see docs).
THINKING_ARG=()
if [ -n "${REASONING_EFFORT:-}" ]; then
  THINKING_ARG=(--default-chat-template-kwargs "{\"enable_thinking\":true,\"reasoning_effort\":\"${REASONING_EFFORT}\"}")
fi

EXTRA_ENV_ARG=()
if [ -n "${EXTRA_ENV:-}" ]; then
  for _kv in $EXTRA_ENV; do EXTRA_ENV_ARG+=(-e "$_kv"); done
  echo "EXTRA_ENV -> $EXTRA_ENV"
fi

# --- fabric ----------------------------------------------------------------
# Deviation from the lab recipe: it runs NCCL_NET_PLUGIN=none over two RoCE
# rails on ONE direct link. Three Sparks are a pairwise QSFP triangle, so we
# use the mesh plugin instead. Do NOT pin NCCL_IB_GID_INDEX here — that block
# belongs to the 2-node single-subnet recipe and is wrong for this topology.
NCCL_ENV=(
  -e NCCL_CUMEM_ENABLE=0
  -e NCCL_NVLS_ENABLE=0
  -e NCCL_CROSS_NIC=0
  -e NCCL_IB_MERGE_NICS=0
  -e NCCL_IGNORE_CPU_AFFINITY=1
  -e NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
  -e TORCH_NCCL_ASYNC_ERROR_HANDLING=1
  -e GLOO_SOCKET_IFNAME="$GLOO_IFACE"
  -e TP_SOCKET_IFNAME="$GLOO_IFACE"
  -e MN_IF_NAME="$GLOO_IFACE"
)
PLUGIN_MOUNT=()
case "$TRANSPORT" in
  mesh)
    test -f "$NCCL_MESH_PLUGIN_DIR/libnccl-net-mesh.so" || test -f "$NCCL_MESH_PLUGIN_DIR/libnccl-net.so" \
      || { echo "no mesh plugin in $NCCL_MESH_PLUGIN_DIR" >&2; exit 2; }
    PLUGIN_MOUNT=(-v "$NCCL_MESH_PLUGIN_DIR:/opt/nccl-mesh:ro")
    NCCL_ENV+=(
      -e NCCL_NET=Mesh
      -e NCCL_IB_DISABLE=1
      -e NCCL_SOCKET_IFNAME="=${GLOO_IFACE}"
      -e NCCL_NET_PLUGIN=mesh
      -e NCCL_ALGO=Ring
      -e NCCL_MESH_DEBUG="${NCCL_MESH_DEBUG:-1}"
      -e LD_LIBRARY_PATH=/opt/nccl-mesh
    )
    ;;
  socket)
    NCCL_ENV+=(-e NCCL_NET=Socket -e NCCL_IB_DISABLE=1 -e NCCL_SOCKET_IFNAME="$GLOO_IFACE")
    ;;
  *) echo "TRANSPORT must be mesh|socket (got $TRANSPORT)" >&2; exit 2 ;;
esac

# The image ENTRYPOINT is ["vllm","serve"], so the model path is the first
# positional argument and there is no --entrypoint override.
DOCKER_ARGS=(
  run --gpus all -d
  --log-opt max-size=20m --log-opt max-file=3
  --name "$NAME" --restart no
  --network host --ipc host --shm-size 32g
  --cpuset-cpus "${CPUSET:-5-9,15-19}"
  --ulimit memlock=-1:-1 --cap-add IPC_LOCK
  --device /dev/infiniband:/dev/infiniband
  -v "$MODEL_HOST_PATH:$MODEL_PATH:ro"
  -v "$DRAFT_HOST_PATH:$DRAFT_PATH:ro"
  -v "$CACHE_HOST_PATH:/cache"
  -v "$CACHE_HOST_PATH/triton:/root/.triton"
  -v "$CACHE_HOST_PATH/b12x:/cache/b12x"
  -v "$CACHE_HOST_PATH/huggingface:/root/.cache/huggingface"
  "${PLUGIN_MOUNT[@]}"
  -e VLLM_HOST_IP="$HOST_IP"
  -e VLLM_CACHE_ROOT=/cache
  -e HF_HOME=/cache/huggingface
  -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1
  -e VLLM_ENGINE_READY_TIMEOUT_S=3600
  -e VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS="${VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS:-1800}"
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
  -e SAFETENSORS_FAST_GPU=1
  -e OMP_NUM_THREADS="${OMP_NUM_THREADS:-16}"
  -e TORCH_CUDA_ARCH_LIST=12.1a
  -e CUTE_DSL_ARCH=sm_121a
  -e CUDA_HOME=/usr/local/cuda
  -e TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
  -e B12X_CACHE_DIR=/cache/b12x
  -e B12X_POLICY_MODE="${B12X_POLICY_MODE:-auto}"
  -e VLLM_PLUGINS=
  -e VLLM_WORKER_MULTIPROC_METHOD=spawn
  -e VLLM_SSM_CONV_STATE_LAYOUT=DS
  -e VLLM_USE_V2_MODEL_RUNNER="${VLLM_USE_V2_MODEL_RUNNER:-1}"
  -e VLLM_ENABLE_PCIE_ALLREDUCE=0
  -e VLLM_USE_FLASHINFER_SAMPLER=1
  -e VLLM_USE_BREAKABLE_CUDAGRAPH=0
  -e VLLM_USE_AOT_COMPILE="${VLLM_USE_AOT_COMPILE:-0}"
  -e VLLM_USE_MEGA_AOT_ARTIFACT="${VLLM_USE_MEGA_AOT_ARTIFACT:-0}"
  -e VLLM_USE_STANDALONE_COMPILE="${VLLM_USE_STANDALONE_COMPILE:-0}"
  -e INSTANTTENSOR_BACKEND=BUFFERED
  -e INSTANTTENSOR_BUFFER_SIZE=67108864
  -e INSTANTTENSOR_CHUNK_SIZE=8388608
  -e INSTANTTENSOR_CONCURRENCY=1
  -e INSTANTTENSOR_IO_DEPTH=3
)
# MXFP8 MTP layer 45 is skipped under dflash (model.py:920-922 drops the spec
# layer from the main model), so DeepGEMM is not on the hot path. It is ON by
# default on GB10 (platforms/cuda.py:675-681 counts family 120 as supported)
# and the lab leaves it on. Set VLLM_USE_DEEP_GEMM=0 only to isolate a fault.
[ -n "${VLLM_USE_DEEP_GEMM:-}" ] && DOCKER_ARGS+=(-e VLLM_USE_DEEP_GEMM="$VLLM_USE_DEEP_GEMM")
DOCKER_ARGS+=("${EXTRA_ENV_ARG[@]}" "${NCCL_ENV[@]}" "$IMAGE")
DOCKER_ARGS+=(
  "$MODEL_PATH"
  --served-model-name "${SERVED_MODEL_NAME:-glm-5.3-flash}"
  --host 0.0.0.0 --port "$PORT"
  --trust-remote-code
  --tensor-parallel-size "$TP_SIZE"
  --pipeline-parallel-size "$PP_SIZE"
  --decode-context-parallel-size 1
  --dcp-comm-backend a2a
  --disable-custom-all-reduce
  --mamba-cache-mode "${MAMBA_CACHE_MODE:-align}"
  $( [ "${PREFIX_CACHING:-1}" = "1" ] && echo --enable-prefix-caching || echo --no-enable-prefix-caching )
  --enable-chunked-prefill
  --dtype bfloat16
  --kv-cache-dtype "${KV_CACHE_DTYPE:-fp8}"
  --quantization modelopt_mixed
  --attention-backend B12X
  --block-size "${BLOCK_SIZE:-256}"
  --linear-backend b12x
  --moe-backend "$MOE_BACKEND"
  --no-enable-flashinfer-autotune
  --load-format instanttensor
  --model-loader-extra-config "{\"instanttensor_copy\":${INSTANTTENSOR_COPY:-false}}"
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.85}"
  --max-model-len "${MAX_MODEL_LEN:-1000000}"
  --max-num-seqs "${MAX_NUM_SEQS:-8}"
  --max-num-batched-tokens "${MAX_NUM_BATCHED_TOKENS:-2048}"
  --reasoning-parser "${REASONING_PARSER:-deepseek_r1}"
  --tool-call-parser glm47 --enable-auto-tool-choice
  --distributed-executor-backend mp
  --nnodes "$NNODES" --node-rank "$NODE_RANK"
  --master-addr "$MASTER_ADDR" --master-port "$MASTER_PORT"
  "${KV_MEM_ARG[@]}"
  "${SPEC_ARG[@]}"
  "${EP_ARG[@]}"
  "${EAGER_ARG[@]}"
  "${HF_OVERRIDE_ARG[@]}"
  "${MM_ARG[@]}"
  "${THINKING_ARG[@]}"
)
[ -n "$HEADLESS" ] && DOCKER_ARGS+=("$HEADLESS")
# shellcheck disable=SC2206
[ -n "${EXTRA_ARGS:-}" ] && DOCKER_ARGS+=(${EXTRA_ARGS})

echo "IMAGE=$IMAGE rank=$NODE_RANK nnodes=$NNODES tp=$TP_SIZE ep=${ENABLE_EP:-1} moe=$MOE_BACKEND k=$DFLASH_NUM_SPEC transport=$TRANSPORT"

if [ "${DRY_RUN:-0}" = "1" ]; then
  printf 'docker'
  printf ' %q' "${DOCKER_ARGS[@]}"
  printf '\n'
  exit 0
fi

docker rm -f "$NAME" 2>/dev/null || true
docker "${DOCKER_ARGS[@]}"

echo "launched $NAME rank=$NODE_RANK host=$HOST_IP"
sleep 2
docker ps --format '{{.Names}} {{.Status}}' | grep "$NAME" || {
  echo "$NAME exited; inspect with: docker logs $NAME" >&2; exit 1; }
