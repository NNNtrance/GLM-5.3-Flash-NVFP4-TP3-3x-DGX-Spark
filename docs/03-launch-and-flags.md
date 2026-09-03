# 03 — Launch command and every flag

This page is the reference for **what we actually run** and **why every single flag is there**.
Cluster bring-up (fabric, NCCL mesh plugin, weights) is in the install docs; the benchmark
numbers in full are in `results/`; the patches themselves are in `patches/`. This page only
covers the launch line and the settings decisions behind it.

Nodes are called `head` (rank 0, serves the API), `worker-1` (rank 1), `worker-2` (rank 2).
Documentation IP addresses are used throughout: `192.0.2.10` / `192.0.2.11` / `192.0.2.12`.
Substitute your own management addresses and your own management interface name
(ours is `enP7s7`, the on-board NIC of the DGX Spark).

---

## 0. The settings every number on this page belongs to

Unless a line says otherwise, every measured number below was taken with:

| | |
|---|---|
| Image | `harem/glm53-lil:t10` (fork build + our TP=3 patches; see `patches/`) |
| Model | `local-inference-lab/GLM-5.3-Flash-NVFP4`, `--quantization modelopt_mixed` |
| Shape | TP=3, PP=1, expert parallel ON, MoE backend `marlin`, attention backend `B12X` |
| KV | `--kv-cache-dtype fp8`, block size 256, **no KV pin** (vLLM profiles) |
| Speculative | DFlash2, k=7, draft TP=1, draft KV `auto` |
| CUDA graphs | ON (full capture) + AOT compile env vars |
| Memory | `--gpu-memory-utilization 0.85` for the speed/quality tables, **0.88 in production** |
| Serving | `--max-num-seqs 8`, `--max-num-batched-tokens 2048`, `--max-model-len 1000000` |
| Sampling | temperature 0 |
| Reasoning | thinking ON, `reasoning_effort: low` |
| Date | 3 September 2026 |

Two caveats that apply to the whole page:

- **The speed and quality tables were taken at `gpu-memory-utilization 0.85`; production is
  0.88.** At 0.88 the accuracy probe was re-run (10/10) and speed re-measured as unchanged,
  but the full speed sweep was not repeated. `[measured-here]` for the probe,
  `[not tested]` for a full re-sweep at 0.88.
- Some decisions (batched tokens, KV pin, block size, k>7) were measured during the earlier
  NVFP4/orca era of this same cluster, on a different image. Those rows say so explicitly.
  Where a decision was **not** re-measured on the current stack, it is marked.

---

## 1. The exact launch command

`scripts/start-lil.sh <rank>` reads `scripts/env.example` (copied to `~/glm3x/.env.lil-t10`)
and renders the lines below. You can see exactly what it will run, without starting anything:

```
DRY_RUN=1 ENV_FILE=~/glm3x/.env.lil-t10 ~/glm3x/scripts/start-lil.sh 0
```

The env file is **derived per node with `sed`, never copied**. Copying it between nodes is a
mistake we have made and paid for: the three copies differ in exactly three fields
(`NODE_RANK`, `HOST_IP`, `NCCL_MESH_PLUGIN_DIR`), and everything else must be identical.

### 1.1 Docker flags

```
docker run --gpus all -d \
  --log-opt max-size=20m --log-opt max-file=3 \
  --name harem_glm53_lil --restart no \
  --network host --ipc host --shm-size 32g \
  --cpuset-cpus 5-9,15-19 \
  --ulimit memlock=-1:-1 --cap-add IPC_LOCK \
  --device /dev/infiniband:/dev/infiniband \
  -v /var/tmp/glm-5.3-flash-lil-nvfp4:/models/glm-5.3-flash-lil-nvfp4:ro \
  -v /var/tmp/dflash2-draft:/models/dflash2-draft:ro \
  -v /var/tmp/glm53-lil-cache:/cache \
  -v /var/tmp/glm53-lil-cache/triton:/root/.triton \
  -v /var/tmp/glm53-lil-cache/b12x:/cache/b12x \
  -v /var/tmp/glm53-lil-cache/huggingface:/root/.cache/huggingface \
  -v ~/glm3x/nccl-mesh:/opt/nccl-mesh:ro \
  harem/glm53-lil:t10
```

Identical on all three nodes.

### 1.2 Container environment

```
VLLM_HOST_IP=192.0.2.10               VLLM_CACHE_ROOT=/cache
HF_HOME=/cache/huggingface            HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1                VLLM_ENGINE_READY_TIMEOUT_S=3600
VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=1800
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
SAFETENSORS_FAST_GPU=1                OMP_NUM_THREADS=16
TORCH_CUDA_ARCH_LIST=12.1a            CUTE_DSL_ARCH=sm_121a
CUDA_HOME=/usr/local/cuda             TRITON_PTXAS_PATH=/usr/local/cuda/bin/ptxas
B12X_CACHE_DIR=/cache/b12x            B12X_POLICY_MODE=auto
VLLM_PLUGINS=                         VLLM_WORKER_MULTIPROC_METHOD=spawn
VLLM_SSM_CONV_STATE_LAYOUT=DS         VLLM_USE_V2_MODEL_RUNNER=1
VLLM_ENABLE_PCIE_ALLREDUCE=0          VLLM_USE_FLASHINFER_SAMPLER=1
VLLM_USE_BREAKABLE_CUDAGRAPH=0        VLLM_USE_AOT_COMPILE=1
VLLM_USE_MEGA_AOT_ARTIFACT=1          VLLM_USE_STANDALONE_COMPILE=1
INSTANTTENSOR_BACKEND=BUFFERED        INSTANTTENSOR_BUFFER_SIZE=67108864
INSTANTTENSOR_CHUNK_SIZE=8388608      INSTANTTENSOR_CONCURRENCY=1
INSTANTTENSOR_IO_DEPTH=3
NCCL_CUMEM_ENABLE=0                   NCCL_NVLS_ENABLE=0
NCCL_CROSS_NIC=0                      NCCL_IB_MERGE_NICS=0
NCCL_IGNORE_CPU_AFFINITY=1            NCCL_DEBUG=WARN
TORCH_NCCL_ASYNC_ERROR_HANDLING=1     GLOO_SOCKET_IFNAME=enP7s7
TP_SOCKET_IFNAME=enP7s7               MN_IF_NAME=enP7s7
NCCL_NET=Mesh                         NCCL_IB_DISABLE=1
NCCL_SOCKET_IFNAME==enP7s7            NCCL_NET_PLUGIN=mesh
NCCL_ALGO=Ring                        NCCL_MESH_DEBUG=1
LD_LIBRARY_PATH=/opt/nccl-mesh
```

`VLLM_HOST_IP` is the **only** per-node value here: `192.0.2.10` on head, `192.0.2.11` on
worker-1, `192.0.2.12` on worker-2. The double `=` in `NCCL_SOCKET_IFNAME==enP7s7` is not a
typo in this document; it is what the launcher renders and what the engine runs with.

### 1.3 vLLM flags

The image entrypoint is `["vllm","serve"]`, so the model path is the first positional
argument.

```
/models/glm-5.3-flash-lil-nvfp4 \
--served-model-name glm-5.3-flash \
--host 0.0.0.0 --port 8000 \
--trust-remote-code \
--tensor-parallel-size 3 \
--pipeline-parallel-size 1 \
--decode-context-parallel-size 1 \
--dcp-comm-backend a2a \
--disable-custom-all-reduce \
--mamba-cache-mode align \
--enable-prefix-caching \
--enable-chunked-prefill \
--dtype bfloat16 \
--kv-cache-dtype fp8 \
--quantization modelopt_mixed \
--attention-backend B12X \
--block-size 256 \
--linear-backend b12x \
--moe-backend marlin \
--no-enable-flashinfer-autotune \
--load-format instanttensor \
--model-loader-extra-config {"instanttensor_copy":false} \
--gpu-memory-utilization 0.88 \
--max-model-len 1000000 \
--max-num-seqs 8 \
--max-num-batched-tokens 2048 \
--reasoning-parser deepseek_r1 \
--tool-call-parser glm47 --enable-auto-tool-choice \
--distributed-executor-backend mp \
--nnodes 3 --node-rank 0 \
--master-addr 192.0.2.10 --master-port 29521 \
--speculative-config {"method":"dflash","model":"/models/dflash2-draft","num_speculative_tokens":7,"kv_cache_dtype":"auto","draft_tensor_parallel_size":1,"draft_load_config":{"load_format":"safetensors"}} \
--enable-expert-parallel \
--hf-overrides {"num_attention_heads":66,"num_key_value_heads":66,"linear_num_heads":66,"text_config":{"num_attention_heads":66,"num_key_value_heads":66,"linear_num_heads":66}} \
--mm-encoder-tp-mode data \
--mm-processor-cache-gb 0 \
--skip-mm-profiling \
--limit-mm-per-prompt {"image": 4, "video": 1} \
--default-chat-template-kwargs {"enable_thinking":true,"reasoning_effort":"low"}
```

### 1.4 What differs per node

Only four things differ between the three nodes. Everything else — every flag, every env
var, the image tag, the mount list — is byte-identical.

| | head | worker-1 | worker-2 |
|---|---|---|---|
| `NODE_RANK` / `--node-rank` | 0 | 1 | 2 |
| `HOST_IP` / `VLLM_HOST_IP` | 192.0.2.10 | 192.0.2.11 | 192.0.2.12 |
| `NCCL_MESH_PLUGIN_DIR` | `/home/$USER/glm3x/nccl-mesh` on that node | same | same |
| extra vLLM flag | none | `--headless` | `--headless` |

`--master-addr` is the **head's management address on every node** (`192.0.2.10`), never a
fabric address. Only rank 0 serves the OpenAI API on port 8000; ranks 1 and 2 run headless.
All three ranks must come up and go down together.

---

## 2. Every flag, with rationale and evidence

Sections are linked where a decision has one; otherwise the rationale is the whole entry.
"Inherited" means: **inherited from the local-inference-lab recipe, not A/B tested by us.**

### 2.1 Shape and parallelism

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--tensor-parallel-size 3` | The model only fits across three nodes with TP; see [3.1](#31-tensor-parallel-3--expert-parallel) | `[reported]` (TP=2 comparison is other people's data) |
| `--enable-expert-parallel` | 288 experts divide exactly by 3 (96 per rank) with no slicing; the routed width 2048 does not divide, so **EP is mandatory at TP=3**. See [3.1](#31-tensor-parallel-3--expert-parallel) | `[measured-here]` |
| `--hf-overrides {...66...}` | 64 attention heads do not divide by 3, so pad to 66 (22 local). `linear_num_heads` must also be 66 because `linear_attn_config.num_heads` is only read at config construction. `moe_intermediate_size` is deliberately absent — EP handles it | `[measured-here]` |
| `--pipeline-parallel-size 1` | Inherited. PP was never run on this cluster | `[not tested]` |
| `--decode-context-parallel-size 1` | Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |
| `--dcp-comm-backend a2a` | Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |
| `--disable-custom-all-reduce` | Inherited from the local-inference-lab recipe, not A/B tested by us. The custom all-reduce path is not valid over a multi-node mesh, but we never measured the alternative | `[not tested]` |
| `--distributed-executor-backend mp` + `--nnodes/--node-rank/--master-addr/--master-port` | The fork's multi-node mode. No Ray. Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |

### 2.2 Kernels and quantization

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--quantization modelopt_mixed` | The checkpoint's own format (three groups: NVFP4 + W4A16_NVFP4 + MXFP8) | checkpoint `lil.yaml` |
| `--dtype bfloat16` | Layers that are not quantized (attention, shared expert) stay BF16 | checkpoint card |
| `--moe-backend marlin` | The **only** SM12x MoE backend in this fork that accepts expert maps (b12x / flashinfer_b12x / cutlass all reject them). **What it costs:** marlin is weight-only, so the checkpoint's W4A4 activation scales are dropped (effectively A16). The quality cost of that is **not measured** and cannot currently be measured, because the b12x MoE+EP arm we built to compare against produced broken output. See [3.1](#31-tensor-parallel-3--expert-parallel) | `[measured-here]` for the backend gate, `[not tested]` for the quality cost |
| `--attention-backend B12X` | The fork's GB10-optimised attention path; carries the sparse-MLA 22-local-head shape via `VALID_HPB` | `[measured-here]` |
| `--linear-backend b12x` | Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |
| `--no-enable-flashinfer-autotune` | Inherited from the local-inference-lab recipe, not A/B tested by us. It was upstream's mitigation for a hang during the NVFP4 era (mean time to failure 38 min → 5 h 12 min); on this stack no FlashInfer kernel is on the hot path, so it is **inert for us** | `[measured-here]` for the NVFP4-era hang mitigation, `[not tested]` on the current stack |
| `B12X_POLICY_MODE=auto` | b12x ships policy tables for a GB10 profile that does not cover our TP=3 shape, so it falls back to heuristics. A cluster-specific profile can be generated with `b12x/tools/generate_gpu_profile.py`; we did not | `[not tested]` — open item |
| `VLLM_SSM_CONV_STATE_LAYOUT=DS` | Tried the DS+HND layout: **no measurable effect**. Left in place | `[measured-here]` |

### 2.3 Memory and KV

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--gpu-memory-utilization 0.88` | Measured up the ladder 0.85 → 0.89. 0.88 gives +11.1% KV pool for identical speed and quality; 0.89 was rejected on host swap. See [3.4](#34-kv-cache-and-memory) and [docs/05-memory-ladder.md](05-memory-ladder.md) | `[measured-here]` |
| no `--kv-cache-memory-bytes` (no KV pin) | vLLM profiles and decides. See [3.4](#34-kv-cache-and-memory) | `[measured-here]` |
| `--kv-cache-dtype fp8` | The NVFP4 KV panel is twice as bad on divergence (FP8 KV KLD 0.025 vs NVFP4 KV 0.055) | `[reported]` (quantization survey) |
| `--block-size 256` | +4.09% KV pool for free, no speed cost. 128 is impossible: DeepGEMM `attention.hpp:320` requires a multiple of `index_kpool(4) x 64 = 256` for fp8 KV. See [3.4](#34-kv-cache-and-memory) | `[measured-here]` (NVFP4-era stack) |
| `--max-num-batched-tokens 2048` | 8192 was measured and rejected: it costs 28% of the pool for no speed. 4096 (the lab default) was never tested. See [3.4](#34-kv-cache-and-memory) | `[measured-here]` for 8192, `[not tested]` for 4096 |
| `--max-num-seqs 8` | 4 → 8 gave +20% aggregate throughput at concurrency 8 during the EXL3 evaluation; carried over. Not re-A/B'd on this stack | `[measured-here]` (EXL3 stack), `[not tested]` here |
| `--max-model-len 1000000` | Vendor claims 1M context; needle-in-a-haystack scored 20/20 at an effective 997,952 tokens | `[measured-here]` |
| `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` | Reduces fragmentation on unified memory. Inherited, not A/B tested by us | `[not tested]` |

### 2.4 Graphs, compile and scheduling

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--enforce-eager` **absent** (full CUDA graph capture) | Graphs give +22–36% single-stream decode and cost 12% of the KV pool. See [3.2](#32-cuda-graphs-versus---enforce-eager) | `[measured-here]` |
| `VLLM_USE_AOT_COMPILE=1`, `VLLM_USE_MEGA_AOT_ARTIFACT=1`, `VLLM_USE_STANDALONE_COMPILE=1`, `VLLM_USE_V2_MODEL_RUNNER=1` | The lab recipe's compile set; flipped **together** with graphs. Never isolated from each other. See [3.2](#32-cuda-graphs-versus---enforce-eager) | `[measured-here]` as a bundle, `[not tested]` individually |
| `VLLM_USE_BREAKABLE_CUDAGRAPH=0` | The fork auto-marks `Glm5NextForCausalLM` breakable. Breakable + PIECEWISE + speculative decode makes the engine **silently reject every draft** (upstream vLLM #53030): acceptance pins to 1.00, throughput collapses, no error is raised. Must stay 0 | `[reported]` (upstream issue), mechanism read from our image source |
| `--enable-chunked-prefill` | Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |
| `--enable-prefix-caching` | A/B'd with it off: acceptance and quality unchanged. Kept on because turning it off buys nothing. See [3.6](#36-load-path-cache-and-format) | `[measured-here]` |
| `--mamba-cache-mode align` | A/B'd against `none`: neutral for speed and acceptance. `align` is the lab default, so we kept it | `[measured-here]` |
| `VLLM_USE_FLASHINFER_SAMPLER=1` | Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |
| `VLLM_ENABLE_PCIE_ALLREDUCE=0` | b12x's PCIe all-reduce path; unused over the mesh fabric. Inherited from the local-inference-lab recipe, not A/B tested by us | `[not tested]` |

### 2.5 Speculative decoding

| Flag / value | Rationale | Evidence |
|---|---|---|
| `"method":"dflash"`, `"num_speculative_tokens":7` | DFlash2 beats the engine's own MTP-4 by 7–23%. k=7 is **fixed by operator decision**. See [3.3](#33-speculative-decoding-dflash2-k7) | `[measured-here]` |
| `"draft_tensor_parallel_size":1` | `speculative.py:1683-1690` accepts only 1 or the target TP. At 1, the head-padding patch already covers the draft's 32/8 → 36/9 shape | source constraint + `[measured-here]` |
| `"kv_cache_dtype":"auto"` (draft) | fp8 draft KV would have given +20% pool but failed to start in three separate variants. See [3.3](#33-speculative-decoding-dflash2-k7) | `[measured-here]` (three failures) |
| `"draft_load_config":{"load_format":"safetensors"}` | instanttensor's borrowed buffer plus our pad/copy in the draft loader segfaults. With safetensors the draft loads in 5.71 s. See [3.6](#36-load-path-cache-and-format) | `[measured-here]` |

### 2.6 Serving behaviour

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--default-chat-template-kwargs {"enable_thinking":true,"reasoning_effort":"low"}` | There is **no thinking-off switch** in this chat template. The effort whitelist is `low`/`high`; anything else silently means `max`. See [3.5](#35-reasoning-thinking-effort-and-sampling) | `[measured-here]` |
| `--reasoning-parser deepseek_r1` | The model card says `deepseek_r1`, `recipes.vllm.ai` says `glm45`; we followed the card. Measured: the parser is **not** the cause of empty-content responses. The lab runs `glm45`; we never A/B'd the two on this stack | `[measured-here]` for the empty-content ruling, `[not tested]` for the A/B |
| `--tool-call-parser glm47 --enable-auto-tool-choice` | Tool calling. Our tool-eval-bench score of 87.8 was obtained with exactly this setting | `[measured-here]` |
| `--trust-remote-code` | The checkpoint carries its own config class (`glm5_next.py`) | required |
| `--served-model-name glm-5.3-flash` | Stable client-side name | convention |
| `--mm-encoder-tp-mode data` | The vision tower has 16 heads and 16 does not divide by 3, so the encoder runs data-parallel. The lab manifest requires this unconditionally | source constraint |
| `--skip-mm-profiling`, `--mm-processor-cache-gb 0`, `--limit-mm-per-prompt {"image":4,"video":1}` | The multimodal profiling step eats memory; these limits were added after an OOM during the NVFP4 era | `[measured-here]` (NVFP4-era crash) |
| `VLLM_ENGINE_READY_TIMEOUT_S=3600` | Startup is long (weights 57 s + JIT + rendezvous, ~5 min total). The default is not enough | `[measured-here]` |
| `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1` | Never reach the network; use the on-disk copy | policy |

### 2.7 Container and host

| Flag / value | Rationale | Evidence |
|---|---|---|
| `--network host --ipc host --shm-size 32g` | RDMA plus multi-process workers; shared memory carries broadcast blocks. Inherited from our NVFP4-era setup, never A/B tested | `[not tested]` |
| `--ulimit memlock=-1:-1 --cap-add IPC_LOCK --device /dev/infiniband` | Required for RDMA (the `nvidia-spark-limits` package also sets memlock unlimited) | required |
| `--cpuset-cpus 5-9,15-19` | Core pinning was tried. **The cores are heterogeneous and no gain was demonstrated.** The setting is still in the line but its justification is weak — you can drop it | `[measured-here]`, result inconclusive |
| `OMP_NUM_THREADS=16` | Thread cap on a 20-core node. Inherited, not A/B tested by us | `[not tested]` |
| `--load-format instanttensor` + `{"instanttensor_copy":false}` | The lab's loader; loads the target model in 57–58 s. **Load path only** — no effect on serving speed. See [3.6](#36-load-path-cache-and-format) | `[measured-here]` |
| `--restart no` | Rank 0 must not restart alone; all three ranks come up together (see the systemd unit) | operational rule |
| `--log-opt max-size=20m --log-opt max-file=3` | Log rotation. Engine logs are large | convention |

**Inherited, never A/B tested by us — the full list (11):**
`--pipeline-parallel-size 1`, `--decode-context-parallel-size 1`, `--dcp-comm-backend a2a`,
`--disable-custom-all-reduce`, `--distributed-executor-backend mp`, `--linear-backend b12x`,
`--enable-chunked-prefill`, `--no-enable-flashinfer-autotune`, `VLLM_USE_FLASHINFER_SAMPLER=1`,
`VLLM_ENABLE_PCIE_ALLREDUCE=0`, `--cpuset-cpus 5-9,15-19` (this last one was tried, and the
gain was not demonstrated).

---

## 3. The decisions, with numbers and what they cost

### 3.1 Tensor parallel 3 + expert parallel

**Why three nodes.** GLM-5.3-Flash at NVFP4 is roughly 186 GB on disk; it does not fit on two
DGX Sparks with a usable context. The published two-node results we surveyed report 22–25.6
tok/s single-stream, a KV pool of 507K–1.22M tokens, and 889 s time-to-first-token at 1M
context — a context that exists on paper but not in practice. Three-node reports give 32–35.2
tok/s and a genuinely usable 1M. `[reported]` — **we never ran a TP=2 production arm
ourselves**, so the two-node numbers on this page are other people's, not ours. The one TP=2
thing we did run was a diagnostic quality arm during the Intel int4 evaluation, which is what
told us a loader bug was TP=3-specific, not a checkpoint defect.

**What TP=3 costs.** The model's geometry does not divide by three:

| | before | after | cost |
|---|---|---|---|
| Attention heads | 64 | 66 (22 local) | ~3.1% wasted compute |
| Shared expert intermediate | 2048 | 2112 | ~3.1% wasted compute |
| Vocabulary embedding | 154,880 | padded to a multiple of lcm(64,3)=192 | negligible |
| Routed experts | 288 | 288, unchanged | **none** |

So roughly 3% of arithmetic is thrown away. `[estimate]` from the geometry, not separately
measured. The real cost of TP=3 is not compute, it is maintenance: several vLLM source
patches must be carried, a custom NCCL mesh plugin is needed for a three-node triangle, and
all ranks must be started and stopped together.

**Why expert parallel is mandatory, not optional.** 288 = 2^5 · 3^2, so it divides by 3
exactly: 96 whole experts per rank, no expert is ever sliced. The routed intermediate width
2048 does **not** divide by 3, and the MoE loader computes its shard offsets from the tensor's
own shape in the file — it never reads the padded value we pass in `--hf-overrides`:

```
loaded_per_rank = loaded_weight.shape[shard_dim] // tp_size
start_offset    = loaded_per_rank * tp_rank
```

Because weight and scale are cut at different granularities, the shards drift: 10 lanes
(1.5%) for NVFP4 group-16, 40 lanes (5.9%) for int4 group-128. **No padding value fixes
this** — 2112 and 2304 were both tried and both failed. Turning EP on removes the problem
entirely, because the experts are handed out whole.

Verify EP is really on by this log line, and only this line:

```
[expert_map_manager.py:245] [EP Rank 0/3] Expert parallelism is enabled. Expert placement strategy: linear. Local/global number of experts: 96/288
```

The `group 'ep:0'` line is printed whether or not EP is enabled and is **not** evidence.

**What EP costs.** On a clean cluster after reboot, EP on vs off averaged 94.18 vs 94.51
tok/s across C1–C8 — **−0.35%, inside noise**. On a cluster that had been up and cycled for
hours the same A/B read −2.6%, and the difference showed up only at concurrency 1–2.
`[measured-here]`, NVFP4-era stack, 1 Sep 2026. Removing the MoE padding (2112 → 2048) while
EP was on changed nothing measurable: 63.48 GiB weights, 5,392,258-token pool, 10/10
accuracy, 12/12 code — the ~1.5 GiB saving we expected did not appear.

**What the MoE backend costs.** `marlin` is the only SM12x MoE backend in this fork that
accepts expert maps, so choosing EP forces choosing marlin. marlin is weight-only, which
means the checkpoint's W4A4 activation scales are dropped and activations run at A16. **The
quality cost of that is not measured** and we currently cannot measure it: the b12x MoE+EP
arm we built as the comparison produced broken output (accuracy 7/10, code 0/12) and is
disabled. This is a known, unquantified cost of the recipe. `[not tested]`

### 3.2 CUDA graphs versus `--enforce-eager`

We ran this A/B twice on this cluster, in both directions.

Settings: image t10 (and t5/t3b for the earlier pair), TP=3 + EP + marlin, DFlash2 k=7, KV
fp8, block 256, no pin, `gpu-memory-utilization 0.85`, `max-num-seqs 8`,
`max-num-batched-tokens 2048`, thinking on + effort `low`, temperature 0, hizset-v2 realistic
code prompts, two rounds, warm. Per-user decode tok/s; C8 total is aggregate.

| Arm | C1 | C2 | C4 | C6 | C8 per user | C8 total | acceptance | KV pool |
|---|---|---|---|---|---|---|---|---|
| **t10 — full graph capture (production)** | **56.9 / 56.5** | 42.7 / 39.5 | 29.5 / 28.8 | 22.9 / 22.8 | 21.8 | 145.8 | 62–65% | 3,860,869 |
| t16 — identical, graphs OFF (`--enforce-eager`) | 47.1 / 45.8 | 39.4 / 41.2 | **30.5 / 30.9** | **24.1 / 24.5** | 22.7 / 21.8 | **155.0 / 145.6** | 62–66% | **4,365,217** |
| t17 — graphs, capture sizes 8 and 16 only | 56.1 / 52.2 | 38.6 / 41.9 | 29.1 / 30.5 | 23.6 / 23.6 | 22.4 | 147.6 | 60–66% | 4,063,768 |

`[measured-here]`, 3 September 2026.

The earlier pair, on the pre-QPAD image (t5 graph+AOT vs t3b eager, same day, same settings)
read C1 48.3 / 46.5 against 35.5 / 35.7. That is the top of the range.

**Summary of the trade:**

- Single stream (C1): graphs are **+22% to +36%** faster. (+22% on the final stack, +36% on
  the earlier pair.)
- Cold single stream: graphs 42.9 / 39.4 / 38.6 tok/s versus eager 32.8 / 33.4 / 31.1 — the
  same direction, roughly +27%.
- C4 and C6: **eager is about 5% faster.**
- C8 aggregate: equal within noise (one eager round read 155.0, the other 145.6 against the
  graph arm's 145.8).
- **What it costs: 11.6% of the KV pool** — 3,860,869 tokens with graphs against 4,365,217
  without. At ~8.6 KB per token that is a real capacity loss, not a rounding difference.
- Quality: identical in both arms (accuracy 10/10, code 12/12).

**The middle ground we measured but did not adopt.** `--cudagraph-capture-sizes 8 16` (t17)
captures only the two batch shapes we actually run. It recovers most of the C1 gain (56.1 /
52.2), behaves like eager from C4 up, and gives back a third of the memory: pool 4,063,768,
which is +5.3% over full capture and −6.9% against eager. It scored 11/12 on the code gate,
on the one item that is known to be sensitive across every arm. This is recorded as "the best
of both" and was **not selected** — production stayed on full capture. `[measured-here]`

**The AOT compile env vars.** `VLLM_USE_AOT_COMPILE`, `VLLM_USE_MEGA_AOT_ARTIFACT`,
`VLLM_USE_STANDALONE_COMPILE` and `VLLM_USE_V2_MODEL_RUNNER` are all set to 1 and were
flipped **as one bundle** together with `ENFORCE_EAGER=0`. We never isolated them from each
other or from the graphs, so no individual number can be attributed to any of them.
`[not tested]` individually. Note also that under `--enforce-eager` there is nothing for them
to feed: the fork puts `Glm5NextForCausalLM` on a fixed list that sets compilation mode NONE,
so **torch.compile was never actually on in any of our arms** — the earlier "compile on vs
off" comparison we thought we had run was eager against eager.

**Why PIECEWISE must not be used.** `VLLM_USE_BREAKABLE_CUDAGRAPH=0` is not cosmetic.
Breakable graphs + PIECEWISE + speculative decode causes the engine to reject every draft
token silently: acceptance length pins to 1.00, throughput drops by roughly the speculative
speedup, and **no error is raised** (upstream vLLM #53030; upstream's own fix PR forces
PIECEWISE → NONE for this configuration). If you see acceptance at 1.00, check this first.
`[reported]`

**Recommendation by use case:**

| Your workload | Setting | What you give up |
|---|---|---|
| Single-user chat / IDE assistant | graphs ON (as shipped) | 11.6% of the KV pool |
| 6–8 concurrent agents | either; eager is ~5% faster at C4–C6 and equal at C8 | with graphs, KV; with eager, 22–36% of single-stream speed |
| Maximum context capacity | `--enforce-eager` | 22–36% single-stream decode |
| Compromise | `--cudagraph-capture-sizes 8 16` | ~5% of the C1 graph gain, 6.9% of the eager pool |

### 3.3 Speculative decoding: DFlash2, k=7

The draft model is `incoai/GLM-5.3-Flash-DFlash2` (BF16), licensed **CC BY-NC-ND 4.0**. We
obtained a project-specific, non-transferable permission for our own use and **we do not
redistribute the draft**; you must obtain your own. See the credits page.

**Why speculative decoding at all.** Turning it off (arm t9, everything else identical)
drops per-user decode from 56.9 to 20.4 tok/s at C1 and aggregate C8 from 145.8 to 74 tok/s.
The model itself is clean without it — t9 scored 10/10 accuracy and 12/12 code four times,
with two runs byte-identical. `[measured-here]`

**Acceptance depends enormously on what the model is writing.** This is the single most
important thing to understand before you read any speed number for this stack.

Settings: engine t17, warm, 4 categories x 6 prompts, C1 sequential then C4 with 4 threads,
`max_tokens=700`, temperature 0, thinking on + effort `low`.

| Content type | C1 decode tok/s (min–max) | C1 acceptance | C4 per user | C4 total | C4 acceptance |
|---|---|---|---|---|---|
| Prose | 20.6 (16.6–24.6) | **13.3%** | 10.8 | 37.5 | 11.3% |
| Code (Python/Rust/Go/TS) | 43.0 (30.9–56.3) | 44.2% | 26.6 | 78.3 | 49.7% |
| Math | 57.0 (47.0–74.7) | 60.3% | 34.1 | 79.9 | 57.2% |
| Structured JSON | 52.1 (42.3–59.0) | 55.3% | 32.2 | 90.4 | 57.5% |

`[measured-here]`, 3 September 2026.

For contrast, the synthetic protocol the community uses (temperature 0, 400 tokens, three
fixed prompts: count to 200, fifty `clamp_XX` functions, hash-map prose) on the same engine:
structured **92.8 tok/s at 94% acceptance**, code-pattern **83.2 at 100%**, prose **28.3 at
24%**. `[measured-here]` Those are the ceiling, not what you will see. **On prose the draft
barely holds at all (13%) and speed falls back to roughly the no-draft level.** Why prose is
so much worse was not investigated — draft training distribution, prompt language and
creative-text uncertainty are all untested hypotheses.

**Why k=7 and not more.** k=8 and k=10 were measured on the NVFP4-era stack (31 Aug,
thinking on + low, aggregate tok/s):

| | C1 | C2 | C4 | C6 | C8 | acceptance length | KV pool |
|---|---|---|---|---|---|---|---|
| k=7 | 47.38 | 65.89 | 93.28 | 113.19 | 133.15 | 4.99–5.30 | 5,392,258 |
| k=8 | 47.34 | 71.13 | 92.86 | 114.60 | 134.52 | 5.16–5.51 | 5,332,675 (−1.1%) |
| k=10 | 47.99 | 71.35 | 90.78 | 109.05 | 130.14 | 5.33–5.99 | 5,217,374 (−3.2%) |

(Item 13 of [08 — What we tried](08-what-we-tried.md) carries a second transcription of
this same 31 August run whose k=7 and k=8 rows differ by up to 2 tok/s from the ones above;
the scan files are gone, so we cannot say which transcription is faithful. Neither changes
the decision below.)

k=8 was ahead on acceptance and at C2/C6 with non-overlapping ranges. **But** the three arms
ran on machines with different uptimes, and the k=7 control run was started and never
recorded, so there is no clean control. **k=7 is fixed by operator decision**: above 7 the
output degraded in an earlier clean test, and no depth study will be run. This is a judgement
call, stated as one — `[measured-here, raw lost]` for the k=7 control, decision taken on
other grounds.

**What the draft costs in KV.** Two ways to read the same thing:

- Structural, NVFP4 era with a fixed 34.21 GiB KV pin: 5,956,867 tokens without the draft
  against 5,392,258 with it — **−9.5%**. The main model uses 512 B per token, the draft 1024 B.
- On the current stack, where the pool is profiled rather than pinned and the uniform page
  size is set by the **largest** KV group, the draft group **is** the largest group, so it
  dictates the page for everyone: t9 (no draft) 5,934,911 tokens against t16 (eager, draft on)
  4,365,217 = **−26%**, and against t7 (graphs, draft on) 3,840,579 = **−35%**.

`[measured-here]`. This is structural, not a bug — but it is why the KV pool on this recipe
looks small for a 1M-context model.

We tried three ways to fix it and all three failed to start:

- fp8 draft KV (t14): block stride 20,537,088 does not match the 2,555,904-byte page, because
  the draft group stops being the largest group and its page stays padded. Would have been
  +20% pool.
- `VLLM_KV_CACHE_LAYOUT=LBNHC` (t14b): this model only permits BLHNC.
- Draft attention on B12X (t14c): "B12X ... block_size not supported".

Parked; a real fix needs a patch making the kernel block equal the manager block.
`[measured-here]` (three failures).

**The draft revision story — an open item, honestly.** Production runs revision `dc77ff1c`
(28 Aug). The publisher released `bf582e4e` on 31 Aug: same config, different weights. We
tested it as arm t13b on the current stack:

| | C1 | C4 | C6 | C8 total | acceptance | KV pool | code gate |
|---|---|---|---|---|---|---|---|
| t10 (`dc77ff1c`, production) | 56.9 / 56.5 | 29.5 / 28.8 | 22.9 / 22.8 | 145.8 | 62–65% | 3,860,869 | 12/12 x3 |
| t13b (`bf582e4e`) | 57.5 / 55.3 | 29.5 / 27.9 | 22.6 / 23.6 | 143.6 | 60–65% | 3,863,768 | 12/12, 11/12, 11/12 |

Speed and acceptance are the same. But **the same task (a spiral matrix problem) was wrong in
both repeats** with the new draft, and correct with the old draft and with no draft at all.
Under greedy verification a draft should not be able to change the output at all; what we
believe is happening is that the target's logits shift very slightly with the batch shape
produced by a different acceptance pattern, and on near-ties the argmax flips. **We did not
analyse the root cause. We reverted to `dc77ff1c` and moved on.** This is an open item, and
it is the same phenomenon as the residual instability noted below. `[measured-here]`

**Related known defect: near-tie flipping.** Even at temperature 0, 9 of 88 tool-eval-bench
scenarios varied between attempts. Probable source: atomic accumulation order in the marlin
MoE, or batch-shape-dependent kernel selection. It is noise-level, not a quality defect, but
if you need strict determinism, run with the draft disabled (arm t9 was byte-identical across
runs). `[measured-here]`

**MXFP8 drafter: parked.** `local-inference-lab/GLM-5.3-Flash-DFlash2-MXFP8` (the same
`dc77ff1c` weights converted to MXFP8) would save about 1 GB of weights. It does not start on
TP=3: with the stock 32/8 head config the loader asserts on divisibility, and with our 36/9
padded config rank 2 fails with `start (768) + length (384) exceeds dimension size (1024)` —
our pad-then-narrow loader only covers the BF16 paths, not the ModelOpt MXFP8 weight+scale
path. A loader patch would fix it; we did not write one. `[measured-here]` (failure),
`[not tested]` (the gain).

### 3.4 KV cache and memory

**Nothing about the KV pool is per-agent.** The pool is consumed by requests that are
currently generating; an idle session occupies nothing. The binding limit is
`--max-num-seqs`, not the pool: with 8 concurrent sequences the worst case is
8 x per-request context. If the pool fills, requests **wait** (there is a
`num_requests_waiting_by_reason{reason="capacity"}` counter) or are preempted and resumed —
a wrong estimate makes things slow, it does not cause OOM.

**KV dtype: fp8.** The NVFP4 KV alternative measures about twice the divergence
(FP8 KV KLD 0.025 vs NVFP4 KV 0.055 in the quantization survey), so fp8 it is. `[reported]`

**No KV pin — profile instead.** We measured both, on the NVFP4-era stack, single variable,
after a clean reboot, `gpu-memory-utilization 0.85`:

| | pinned | profiled (vLLM decides) |
|---|---|---|
| KV space | 34.21 GiB | 26.76 GiB |
| Pool | 5,392,258 tokens | 4,217,735 tokens |
| 1M-context multiple | 5.14x | 4.02x |

Pinning is worth **+21.8% of the pool**. `[measured-here]` So why do we not pin?

- The 7.45 GiB you take back is vLLM's working space (activations, CUDA context, network
  buffers). Pinning means running with **no activation safety margin**; effective utilisation
  becomes ~0.92 with no seatbelt.
- With a pin in place, `profile_cudagraph_memory()` is never called — so pinning and CUDA
  graphs together are actively dangerous, and graphs are on in production.
- Giving the memory back helped only the worker nodes. Free memory after startup was head
  **0.8 GB**, worker-1 6.7 GB, worker-2 6.1 GB — the node that is actually tight got almost
  nothing.
- The failure mode when a pin is too aggressive is not a clean OOM. We locked a node for 17
  minutes printing `No available shared memory broadcast block`; the kernel OOM killer never
  fired, ping and TCP answered but sshd could not get scheduled, and the machine had to be
  power-cycled. `[measured-here]`

Rule we now follow: **while you are still tuning, do not pin.** Raise
`--gpu-memory-utilization` instead and let the profiler decide — that keeps the buffer and
keeps most of the pool.

**Memory fraction: 0.88.** Measured up the ladder on the current stack, no pin, t10,
one rung at a time. Full table in [docs/05-memory-ladder.md](05-memory-ladder.md); the
summary:

| Fraction | KV pool | vs base | 1M-context multiple | head free RAM | head swap | quality |
|---|---|---|---|---|---|---|
| 0.85 (base) | 3,881,159 | — | 3.88x | 6.4–7.6 GB | 0 | 10/10, 12/12 |
| 0.86 | 4,023,188 | +3.7% | 4.02x | 7.0 GB | 41 MB | 10/10 |
| 0.87 | 4,156,521 | +7.1% | 4.16x | 5.8 GB | 47 MB | 10/10 |
| **0.88 (production)** | **4,310,144** | **+11.1%** | **4.31x** | 4.6–4.7 GB | 439–456 MB | 10/10 |
| 0.89 (rejected) | 4,408,695 | +13.6% | 4.41x | 5.2 GB (swap-inflated) | 927 MB | 10/10 |

`[measured-here]`, 3 September 2026. **What it costs:** speed and quality are unchanged at
every rung; what you spend is host RAM headroom. 0.89 was rejected because its apparent free
memory is produced by swapping — the head node's swap doubles. The device ceiling with no pin
is about 0.915 (111.3–111.8 GiB visible free); the binding constraint is host RAM, and our
rule is MemAvailable >= 2 GiB. Arithmetic for planning: **KV is about 8.6 KB per token**
(35.98 GiB for 4.37M tokens). Post-reboot production verification read 4,321,739 tokens,
accuracy 10/10.

**Block size 256.** Single variable, 2304 → 256, marlin, DFlash2 k=7, KV 34.21 GiB pinned,
concurrency 8, two rounds each:

| | block 2304 | block 256 |
|---|---|---|
| KV pool | 5,180,511 | **5,392,258** (+4.09%) |
| 1M-context multiple | 4.94x | 5.14x |
| Final startup stage | 97.66 s | 99.55 s |
| Accuracy | 10/10 | 10/10 |

Speed across ten comparisons averaged +0.9% for 256, inside the ±4% band — i.e. free.
`[measured-here]` (NVFP4-era stack). **128 is not available**: DeepGEMM `attention.hpp:320`
requires the block to be a multiple of `index_kpool(4) x 64 = 256` for fp8 KV, and the engine
dies on an assert at startup. 256 is the smallest legal value. Larger values were also
rejected: 5632 pads the KDA/mamba page by roughly 79%.

**`--max-num-batched-tokens 2048`.** 8192 was tried after seeing it recommended in a two-node
recipe. Measured with the pin removed so vLLM would profile:

| | 2048 | 8192 |
|---|---|---|
| KV memory | 36.21 GiB | 28.38 GiB |
| Pool | 5,484,559 | 3,949,381 (**−28%**) |
| 1M-context multiple | 5.23x | 3.77x |
| C8 TTFT | 3.70 s | 4.44 s |

Speed across C1–C8 was mixed (+1.8, +4.4, −5.9, +4.9, −2.7%), all within the repeatability
band. `[measured-here]` (NVFP4-era stack, 96 code prompts, 256-token outputs). The reason it
does nothing for us: our decode step consumes roughly 48 tokens (6 sequences x 8 draft
tokens), nowhere near the 2048 ceiling, and our prompts are short enough that prefill
chunking is not the bottleneck. A two-node recipe with a 3.2 GB KV pin and 262K context has a
different bottleneck; do not carry this lever between boxes without re-measuring.

**Be explicit about what was and was not tested on this stack:** 8192 was tested during the
NVFP4 era and rejected on pool cost. **4096 — the value the lab recipe renders — has never
been tested, on either stack.** We run 2048 by inheritance from our own earlier measurement,
not from a comparison against the lab default. `[not tested]`

This experiment also cost us a node: the first attempt ran 8192 **with the KV pin still on**,
leaving only 7.74 GiB for activations. The profiling step did not fit, the system drowned in
swap, and the machine had to be unplugged. **Order matters: remove the pin before you measure
anything that changes the profiling step.**

**`--max-num-seqs 8`.** During the EXL3 evaluation, 4 → 8 gave about +20% aggregate
throughput at concurrency 8 and removed queueing. Carried over to this stack without a
re-run. `[measured-here]` on the EXL3 stack, `[not tested]` here. Note this is the flag that
actually caps concurrency — the KV pool does not.

**`--max-model-len 1000000`.** Needle-in-a-haystack scored **20/20** across 4 haystack sizes
(1K / 325K / 650K / 974K tokens) x 5 depths, at an effective context of 997,952 tokens (the
engine's 1M ceiling). 8.27M tokens of prefill at ~1,560 tok/s average, which does not degrade
as context grows; a 1M-token request takes about 11 minutes to prefill. `[measured-here]`
That test is single-fact retrieval — the easy end. It does not measure multi-needle retrieval
or long-context reasoning.

### 3.5 Reasoning: thinking, effort and sampling

**There is no thinking-off switch in this model's chat template.** This is the single most
commonly wasted day on this stack, so it is stated plainly:

- The checkpoint's `chat_template.jinja` **never mentions `enable_thinking`**. It has only
  `reasoning_effort` and `clear_thinking`, and the rendered prompt always ends with
  `<|assistant|><think>`.
- Line 2 of the template reads, in effect, `effective_reasoning_effort = reasoning_effort if
  reasoning_effort in ['low','high'] else 'max'`. So `medium` and `none` **silently mean
  `max`**, and omitting the field entirely also means `max`.
- Passing `enable_thinking: false` does not turn thinking off. On this stack it is simply
  inert (A/B/C produce the same output). On the earlier orca checkpoint it was actively
  harmful: it disabled the **extraction filter** while the model kept thinking, so reasoning
  leaked into the answer — outputs like `392392` and `Paris.Paris`.
- An older third-party checkpoint's template **did** honour `enable_thinking`, which is why
  an early measurement of ours appeared to show it working. That measurement was
  template-specific and has been **retracted**.

`[measured-here]`. Our standing rule is that `enable_thinking=false` is never used.

**We run effort `low` by default, and the model manages its own think length well there.**
The cost of having thinking on at all is zero: same engine, C1 28.94 vs 28.91 tok/s, C8 94.04
vs 97.11. `[measured-here]` What effort actually changes is how long the model thinks:

| Task | low tokens | max tokens | ratio | low seconds | max seconds |
|---|---|---|---|---|---|
| Easy tool call | 8 | 144 | **18x** | 0.3 | 3.6 |
| GSM8K question | 88 | 405 | 4.6x | 1.8 | 8.6 |
| Constrained writing (IFEval-style) | 198 | 936 | 4.7x | 7.6 | 20.7 |
| Code (palindrome function) | 509 | 4,112 | 8.1x | 8.0 | 73.5 |

Settings: t10, temperature 0, 4 prompts, 3 September 2026. `[measured-here]`

So `max` is not "think without limit" — it is 4–8x the tokens depending on task difficulty
(mean around 6x), and 5–12x the wall time. An earlier "20x" estimate of ours was wrong and is
**retracted**. This is also why every quality benchmark in `results/` was run at effort
`low`: at `max` the full MMLU run alone would take most of a day.

**The empty-content phenomenon, and why it matters for agents.** At effort `low` on trivial
questions the model can finish inside the reasoning block without ever emitting `</think>`.
The parser is behaving correctly — with no closing tag, everything is reasoning — but the
client sees `content` empty and the answer sitting in `reasoning`. On the earlier orca stack
this happened in about 1 request in 9 (4/36 = 11%). The fix costs nothing: one mandatory
system line.

```
Always write the final answer as your reply, outside your reasoning.
```

With that line: 0/100 empty, mean output 6.5 tokens against 6.3 without. `[measured-here]`
Raising the baseline to `high` also fixes it (0/48) but makes every trivial task pay three
times the effort, so it was rejected.

On the **current** stack we could not reproduce the phenomenon at all: 40 assorted trivial
questions x effort low x temperature 0 gave 0/40 empty, and 3 questions x 4 settings x 6
repeats gave 0/72. `[measured-here]`, 3 September 2026. We still ship the system line — it is
free insurance, and an agent client should additionally fall back to `reasoning` when
`content` is empty, because the failure is silent: no error, nothing in the log.

**Reasoning parser.** We run `deepseek_r1` because the model card says so; `recipes.vllm.ai`
and the lab recipe say `glm45`. Two official sources disagree and we followed the card. We
did measure one thing: **the parser is not the cause of empty content** (after switching, 8/10
were still empty on the affected stack). We never A/B'd `deepseek_r1` against `glm45` on this
stack. `[not tested]` — open item.

**Sampling: choose it per agent, not per cluster.** Official recommendation for this model is
temperature 1.0 with top_p 0.95; the checkpoint's `generation_config.json` is empty, so the
server falls back to vLLM defaults. Speed is essentially independent of temperature
(C1, warm, effort low, 2 prompts each):

| | code | JSON | prose | acceptance (code/JSON/prose) |
|---|---|---|---|---|
| T=0 | 55.7 | 56.5 | 23.3 | 66 / 63 / 18% |
| T=0.6, top_p 0.95 | 55.9 | 59.9 | 23.8 | — |
| T=1.0, top_p 0.95 | 52.9 | 58.4 | 22.4 | 60 / 66 / 17% |

`[measured-here]` — a ±5% spread, inside noise. DFlash2's rejection sampling is robust to
temperature. **So pick temperature for quality, never for speed.** Each request carries its
own temperature and top_p over the OpenAI API, so set it per agent: 0–0.3 for code and tool
agents where you want repeatability, 0.7–1.0 for writing and ideation, 1.0 / 0.95 as the
general default. Note that temperature 0 is **not** bit-deterministic while the draft is on
(see the near-tie flipping above) — seed plus low temperature reduces variance, it does not
guarantee zero.

### 3.6 Load path, cache and format

| Setting | What it does | Evidence |
|---|---|---|
| `--load-format instanttensor` + `{"instanttensor_copy":false}` | Loads the 186 GB target in **57–58 s** (482M chunks at 4.24 GB/s). Total load 76 s / 61.9 GiB including the draft. **Load path only** — no effect on serving speed | `[measured-here]` |
| `"draft_load_config":{"load_format":"safetensors"}` | The draft must **not** use instanttensor. Its borrowed buffer plus our pad/copy in the draft loader segfaults at startup. With safetensors the draft loads in 5.71 s | `[measured-here]` |
| `--mamba-cache-mode align` | A/B'd against `none`: neutral for speed and acceptance. `align` is the lab default and stores KDA states at block boundaries, which is the correct pairing with prefix caching on. `none` was a shortcut left behind during debugging | `[measured-here]` |
| `--enable-prefix-caching` | A/B'd off as a diagnostic: acceptance 44–52% (identical), pool 3,952,522, code 8/12 (within the arm-to-arm noise of that image). Neutral, so it stays on | `[measured-here]` |

Alternative loaders were tried and rejected: `runai_streamer` was **2–5x slower**, and
`fastsafetensors` does not install on this hardware. `[measured-here]`

**The prefix-cache measurement artefact — read this before benchmarking.** With prefix
caching on, sending the same prompt twice skips prefill the second time. A long prompt's TTFT
went 8.59 s → 5.37 s on the repeat purely from cache. Prefill and decode differ by roughly
forty times on this stack (~1,300–1,600 tok/s prefill against ~20–57 tok/s decode), so a
cached prefill dominates any short measurement. Cold and warm single-stream numbers on the
same arm can differ by 30% or more (arm t8 read cold 43.7 / warm 59.0 / 44.3 tok/s). Our
measurement rule, which the numbers on this page follow:

- Both arms of a comparison run in the **same session, back to back**, with no heavy I/O in
  between.
- Both arms send the **same env lines and the same `chat_template_kwargs`** — a mismatched
  `reasoning_effort` silently means `max` on one arm and has invalidated a comparison of ours
  before.
- **Cold and warm runs are reported separately and never averaged together.**
- Per-level repeatability band on this cluster is 1.5–7%; treat anything smaller as noise.

Also note that prefix caching only pays off if the **beginning** of the conversation stays
byte-stable. Editing a system prompt or rewriting history mid-conversation invalidates the
cache from that point on, so agent frameworks on this stack should append, not edit.

---

## 4. How to adapt this recipe

Each row states what to change and what it costs. Anything marked `[estimate]` has not been
measured by us in that configuration.

### Running on 2 nodes instead of 3

We did not do this, so we cannot hand you a working line. What we can tell you from the
survey and from our own shape work:

- Drop `--enable-expert-parallel` only if you have verified your MoE backend's shard
  arithmetic at TP=2. 288 divides by 2 as well as by 3, so EP remains available and is
  probably still the right choice.
- Drop the `--hf-overrides` head padding: 64 heads divide by 2, so none of the TP=3 padding
  is needed, and most of the patches in `patches/` become unnecessary.
- Set `--nnodes 2` and use only ranks 0 and 1.
- Expect: single-stream decode around 22–25.6 tok/s and a KV pool in the 507K–1.22M range,
  which means 1M context is not usable in practice — one published two-node result measures
  889 s TTFT at 1M with 1.24 GB of free memory left. `[reported]`, not ours.
- Reduce `--max-model-len` to something the pool can actually serve (262K is what the mature
  two-node recipes pin), and re-measure `--max-num-batched-tokens`: at 262K with a small
  pinned KV, prefill chunking **is** the bottleneck, which is exactly the regime where 8192
  was reported to help. Our 2048 result does not transfer.

### Maximum KV pool / longest usable context

| Change | Gain | What it costs |
|---|---|---|
| `--enforce-eager` | pool 3.86M → 4.37M tokens (+13%) | 22–36% single-stream decode `[measured-here]` |
| Disable speculative decoding (drop `--speculative-config`) | pool → 5.93M tokens (+35% again) | C1 56.9 → 20.4 tok/s, C8 total 145.8 → 74 `[measured-here]` |
| Raise `--gpu-memory-utilization` toward 0.89–0.915 | +2 to +5% pool per rung | host swap; 0.89 already doubles head swap to 927 MB `[measured-here]`, above that untested |
| Add a KV pin | +21.8% pool | no activation safety margin, and `profile_cudagraph_memory()` is never called — **do not combine with CUDA graphs** `[measured-here]` |

Stacking eager + no draft gets you to roughly 5.9M tokens, i.e. 5.9x a 1M-context request, at
about a third of the decode speed. That is the honest shape of the trade.

### Lowest latency (single user)

| Change | Gain | What it costs |
|---|---|---|
| Keep graphs on (default) | +22–36% C1 | 11.6% of the pool `[measured-here]` |
| Keep effort at `low` | 4–8x fewer thinking tokens, 5–12x less wall time than `max` | quality on hard tasks; GSM8K at low is 94.0% `[measured-here]` |
| Feed it code/JSON/math rather than prose | 43–57 tok/s instead of 20.6 | not a knob, just a fact about the draft `[measured-here]` |
| `--max-num-seqs 1` | nothing measured | `[not tested]` — the pool is not the concurrency limit, this flag is |

Best single-stream number we have recorded on this recipe: **59.6 / 59.4 tok/s at C1, TTFT
0.38 s**, on a clean cluster immediately after reboot with the production automatic startup.
`[measured-here]` Warm numbers on a cluster that has been cycled several times read 5–10%
lower; reboot before you benchmark.

### Maximum throughput (many concurrent agents)

| Change | Gain | What it costs |
|---|---|---|
| `--enforce-eager` | ~+5% at C4–C6, equal at C8, +13% pool | 22–36% single-stream `[measured-here]` |
| `--cudagraph-capture-sizes 8 16` | C1 near full-graph, C4+ near eager, +5.3% pool over full graph | ~5% of the C1 graph gain `[measured-here]` |
| Raise `--max-num-seqs` above 8 | untested on this stack | `[not tested]`; 4 → 8 was worth +20% at C8 on the EXL3 stack |
| Raise `--gpu-memory-utilization` to 0.88 (already default here) | +11.1% pool, more headroom for concurrent long contexts | 439–456 MB head swap `[measured-here]` |

Best aggregate we have recorded: **152.8 / 152.0 tok/s total at concurrency 8**, clean cluster
after reboot, TTFT 1.02 s. `[measured-here]`

---

## 5. Open items on this page

- `--max-num-batched-tokens` 2048 versus the lab's 4096: **never tested**, on either stack.
- `--reasoning-parser deepseek_r1` versus the lab's `glm45`: never A/B'd here.
- The quality cost of marlin dropping W4A4 activation scales to A16: unmeasured, and
  currently unmeasurable because the b12x MoE+EP comparison arm produces broken output.
- The `bf582e4e` draft revision's deterministic wrong answer on a near-tie: root cause not
  analysed; we reverted rather than investigated.
- `B12X_POLICY_MODE=auto`: b12x's shipped GB10 policy tables do not cover the TP=3 shape, so
  kernel selection is heuristic. A cluster-specific profile could be generated and was not.
- fp8 draft KV (+20% pool) is parked behind a patch that makes the kernel block equal the
  manager block.
- The eleven inherited flags listed at the end of section 2 have never been varied by us.
