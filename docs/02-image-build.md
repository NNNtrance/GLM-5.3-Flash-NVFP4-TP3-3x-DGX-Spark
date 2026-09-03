# 02 — Building the engine image

This page builds the container that serves the model. It is one Docker image, assembled
in five layers, and it must be built on the `head` node and copied to `worker-1` and
`worker-2` byte-identically. Nothing here starts an engine; serving flags are in
[03-launch-and-flags.md](03-launch-and-flags.md).

Everything in this page was done on our own three nodes on 2–3 September 2026. Sources for
every layer are in [../patches/](../patches/).

We do not redistribute the image. The base image contains NVIDIA CUDA, cuDNN and NCCL
libraries under NVIDIA's own redistribution terms, so you build your own from the same
pinned inputs.

---

## 1. Why there is an image at all, and why TP=3 needs patches

Read this section even if you plan to skim the rest — the rest is unreadable without it.

`zai-org/GLM-5.3-Flash` is a mixture-of-experts model with these shape numbers
(from the checkpoint's `config.json`):

| Field | Value | ÷ 3 ? |
|---|---|---|
| `num_hidden_layers` | 45 | fine |
| `num_attention_heads` / `num_key_value_heads` / `linear_num_heads` | **64** | 64 % 3 = **1** |
| `head_dim` | 128 | n/a |
| `vocab_size` | **154880** | 154880 % 3 = **2** |
| `intermediate_size` (dense) | 12288 | 12288 / 3 = 4096, fine |
| `moe_intermediate_size` (routed expert width) | **2048** | 2048 % 3 = **2** |
| `n_shared_experts` | 1 (× 2048 wide, BF16) | 2048 % 3 = **2** |
| routed experts | 288 | 288 / 3 = 96, fine |
| `num_nextn_predict_layers` | 1 (MTP, layer 45) | n/a |

Tensor parallelism cuts each of those dimensions into `world_size` equal slices. With
two or four GPUs, 64 heads split cleanly (32 or 16). With **three** GPUs nothing splits:
64 heads leave a remainder of 1, the vocabulary leaves 2, the BF16 shared expert leaves 2.
The engine does not try to be clever about it — it asserts and stops:

```
vllm/models/glm5next/nvidia/model.py:760-762
    assert config.num_attention_heads % world_size == 0        # 64 % 3 = 1
vllm/models/glm5next/nvidia/attention.py:77-80
    ValueError: num_heads must be divisible by tensor parallel size
```

The lab's own launcher refuses even earlier, before any Python runs
(`lil/internal/launcher/builder.go:145`).

The standard fix is **padding**: round the dimension up to a multiple of 3, give the extra
rows zeros, and throw the extra rows away after the maths. 64 heads become 66 (22 per
node), the vocabulary is padded to 154944, the BF16 shared expert becomes 2112. The routed
experts are **not** padded — 288 divides by 3, so they are distributed by *expert
parallelism* (96 experts per node) instead of being sliced.

Padding creates a second problem, which is where most of our night went. Once the engine
runs at 22 local heads, it hands 22 to attention kernels that were written and validated
for head counts divisible by 8 or 16. Some of those kernels raise a clear error (good).
One of them — the b12x sparse-MLA **decode** kernel — accepted 22, ran, and returned
**silently wrong numbers**. That is the single most important finding on this page.

---

## 2. Image lineage

Five tags, each one a thin layer on the previous one. The chain was read back with
`docker history harem/glm53-lil:t10`.

```
vllm/vllm-openai:glm53-flash-arm64-cu130      base, 30.7 GB
  └─ harem/glm53-lil:t2     full source build of the LIL fork for sm_121a
      └─ harem/glm53-lil:t3     HAREM-TP3-LIL          shape padding for TP=3
          └─ harem/glm53-lil:t3d    HAREM-TP3-LIL-C4       uniform KV page rounding
              └─ harem/glm53-lil:t3e    HAREM-B12X-PREFILL-HPAD  prefill 22 -> 24 heads
                  └─ harem/glm53-lil:t10    HAREM-B12X-QPAD      decode/extend 22 -> 24 heads
```

`t4` and `t4b` branch off `t3e` and are **not** part of the production chain; they are the
b12x MoE + expert-parallel attempt, kept because it failed in an instructive way
(section 8).

The gap between `t3e` and `t10` in the tag numbering is real: `t4`–`t9` were experiment
arms (EP, speculation off, CUDA graphs) that either failed or are covered elsewhere.

### Inputs, pinned

| Component | Source | Exact revision | License |
|---|---|---|---|
| Base image | Docker Hub `vllm/vllm-openai:glm53-flash-arm64-cu130` | `sha256:905c02933be6021301db2dc284e24e3727467aa3a0f63b41d609885778a07bce` (arm64/linux, created 2026-08-26, 30.7 GB, ships vLLM `0.1.dev20051+g487ecf187`, torch 2.13.0+cu130, nvcc 13.0.88) | Apache-2.0 (vLLM); bundled NVIDIA libraries under NVIDIA terms — **do not redistribute the image** |
| vLLM fork | <https://github.com/local-inference-lab/vllm>, branch `dev/jovian-judgement` | `9c4dd05487629eccb26d7166459867a3db9b099f` | Apache-2.0 |
| b12x kernels | <https://github.com/local-inference-lab/b12x> | `887607b26f952f7cc13b5ad4ef720627eced0486` (`pyproject.toml` version 1.3.0) | Apache-2.0 |
| instanttensor | PyPI | `>=0.1.9` — **we did not pin an exact version** and cannot state which one we installed. Pin it yourself before you rely on a reproducible build. `[not tested]` | Apache-2.0 (see [../CREDITS.md](../CREDITS.md)) |
| `lil` launcher | <https://github.com/local-inference-lab/lil> `cb58d54` | reference only, not used in production (it refuses TP=3) | Apache-2.0 |

The image tag prefix `harem/` is our own; see the README for why the name is hardcoded in
several places.

### Why a full source build, and not "reuse the base image's compiled libraries"

The base image was built from `487ecf187`, a `vllm-project/vllm` PR branch that left main
on 2026-08-12. The fork is rebased on main at `299ebd094` (2026-08-25). The fork's own 75
commits touch no compiled source at all — but the 13 days of upstream C++/CUDA churn
between the two bases do:

```
git diff 487ecf187 9c4dd0548 -- csrc cmake CMakeLists.txt setup.py
27 files changed, 4669 insertions(+), 152 deletions(-)
```

and the change is not additive. `csrc/libtorch_stable/torch_bindings.cpp` alters the
**registered operator schemas**: `situ_and_mul_quant` and `fused_gdn_decode_post_conv_mtp`
are new, `situ_and_mul` gained a `Tensor? valid_rows` argument, and
`fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out` was removed. Reusing the base
image's prebuilt `_C_stable_libtorch.abi3.so` would give the fork's Python an operator
table that does not match what it calls. So `t2` rebuilds the extensions from the fork
tree for `sm_121a`. `[measured-here]`

One consequence to know before you read a build log: on CUDA the fork does **not** build a
module called `vllm._C` (`setup.py:1420-1423` appends only `_C_stable_libtorch` and
`_moe_C_stable_libtorch`). `import vllm._C` failing is correct, not a build failure.

---

## 3. Build commands, in order

Run all of this on `head`. You need roughly 120 GB of free disk for the build cache and
the image tree.

### 3.1 Fetch the pinned inputs

```
mkdir -p ~/glm53-build && cd ~/glm53-build
```

```
git -c http.version=HTTP/1.1 clone https://github.com/local-inference-lab/vllm.git vllm-lil
```

```
git -C vllm-lil checkout 9c4dd05487629eccb26d7166459867a3db9b099f
```

```
git -c http.version=HTTP/1.1 clone https://github.com/local-inference-lab/b12x.git b12x
```

```
git -C b12x checkout 887607b26f952f7cc13b5ad4ef720627eced0486
```

`http.version=HTTP/1.1` is not decoration. GitHub's HTTP/2 endpoint answered 401 on our
network, which breaks every CMake `FetchContent` clone during the build as well; the
Dockerfile sets it globally inside the image for the same reason. `[measured-here]`

### 3.2 Pull the base image by digest

```
docker pull vllm/vllm-openai@sha256:905c02933be6021301db2dc284e24e3727467aa3a0f63b41d609885778a07bce
```

```
docker tag vllm/vllm-openai@sha256:905c02933be6021301db2dc284e24e3727467aa3a0f63b41d609885778a07bce vllm/vllm-openai:glm53-flash-arm64-cu130
```

Pull by digest, not by tag. If that tag is ever moved or deleted the recipe's first step
breaks; the digest is the only durable handle we have. We do not know who publishes that
tag or how long it will exist. `[not tested]`

### 3.3 Stage the patch sources

```
git clone https://github.com/NNNtrance/GLM-5.3-Flash-NVFP4-TP3-3x-DGX-Spark.git ~/recipe
cp -r ~/recipe/patches/* ~/glm53-build/
```

```
cp ~/glm53-build/dockerignore.base ~/glm53-build/.dockerignore
```

The per-layer `Dockerfile.tX.dockerignore` files are already in place. BuildKit reads
`<dockerfile>.dockerignore` when it exists, which is how the t3…t10 layers get a two-file
build context instead of the 742 MB checkout.

### 3.4 t2 — the full source build

```
cd ~/glm53-build && docker build --provenance=false --sbom=false --build-arg HAREM_STAMP="$(date -Is)" -t harem/glm53-lil:t2 . 2>&1 | tee build-t2.log
```

`--provenance=false --sbom=false` keeps the result a plain image rather than a manifest
list, so `docker image inspect --format '{{.Id}}'` returns a comparable ID on all three
nodes. Without it the ship-and-compare check in section 4 is meaningless.

Then the GPU-side exam, which the build itself cannot run (a `docker build` has no GPU):

```
cd ~/glm53-build && ./verify-t2-gpu.sh harem/glm53-lil:t2
```

### 3.5 t3, t3d, t3e, t10

Each is one `docker build` and each ends with its own build-time exam. They are seconds,
not minutes.

```
cd ~/glm53-build && docker build --provenance=false --sbom=false -f Dockerfile.t3 --build-arg HAREM_STAMP="$(date -Is)" -t harem/glm53-lil:t3 .
```

```
cd ~/glm53-build && docker build --provenance=false --sbom=false -f Dockerfile.t3d --build-arg HAREM_STAMP="$(date -Is)" -t harem/glm53-lil:t3d .
```

```
cd ~/glm53-build && docker build --provenance=false --sbom=false -f Dockerfile.t3e -t harem/glm53-lil:t3e .
```

```
cd ~/glm53-build && docker build --provenance=false --sbom=false -f Dockerfile.t10 -t harem/glm53-lil:t10 .
```

`t3e-kur.sh` and `t10-kur.sh` do the last two builds *and* the distribution *and* the
identity check in one command; `dagit.sh <tag>` does distribution alone for `t3` and
`t3d`, which have no wrapper. ("kur" and "dagit" are Turkish for "set up" and
"distribute"; the file names are kept as they were built.)

### 3.6 Ship to the workers and prove the three images are identical

```
cd ~/glm53-build && WORKER_1=192.0.2.11 WORKER_2=192.0.2.12 ./dagit.sh t10
```

```
docker image inspect harem/glm53-lil:t10 --format '{{.Id}}'
```

Run that last command on all three nodes and compare. `t3e-kur.sh` / `t10-kur.sh` do the
comparison for you and write the verdict to `/var/tmp/<tag>-status.txt`. Do not skip it:
a worker running a different image than `head` produces a cluster that starts, serves, and
is wrong.

Send the image over the QSFP fabric addresses, not the management LAN.

### 3.7 Expected time and size

All `[measured-here]`, one GB10 node, `MAX_JOBS=12`, `NVCC_THREADS=2`:

| Step | Time | Note |
|---|---|---|
| t2, cold ccache | ~40 min of compilation | Our third attempt ran 2358 s and then failed on `cusparse.h`; that fix is now in the Dockerfile, so budget 45–60 min for a clean cold run |
| t2, warm ccache (our successful run) | **9 min 9 s** total: 203 s extension compile, 288 s image export | The export is not negligible — it is 47.6 GB of layers |
| t3 / t3d / t3e / t10 | **under 10 s each** | They rewrite Python text files; no compilation |
| `verify-t2-gpu.sh` | ~1 min | includes one real b12x JIT compile |
| Ship one image to one worker | **~2 min 40 s** | `docker save \| ssh docker load` over a 200G link, 47.6 GB |

Sizes, from `docker images` and `docker history`:

| Image | Size | Added by this layer |
|---|---|---|
| base | 30.7 GB | — |
| t2 | 47.6 GB | rebuilt extensions + b12x + instanttensor |
| t3 | 47.6 GB | 303 kB patch + 14.9 MB exam bytecode |
| t3d | 47.6 GB | 139 kB + 2.79 MB |
| t3e | 47.6 GB | 90 kB |
| t10 | 47.6 GB (`sha256:437f8385adfd…`) | 188 kB |

The four patch layers together add well under 20 MB. If your `t10` is materially larger
than your `t2`, something rebuilt that should not have.

---

## 4. How the patches are written (applies to all of them)

Every patch is a small Python script that rewrites text files inside the installed vLLM or
b12x tree. All of them follow the same four rules, and it is worth adopting the rules if
you write your own:

1. **Anchored.** Each edit matches a literal block of source and asserts it occurs
   **exactly once**. Zero matches or two matches abort the script before anything is
   written. This is what makes a patch fail loudly when the upstream source moves, instead
   of applying somewhere plausible and wrong.
2. **Idempotent.** Re-running on an already patched tree is a no-op that exits 0.
3. **Marked.** Every inserted block carries a marker string — `HAREM-TP3-LIL`,
   `HAREM-B12X-QPAD`, and so on. The markers are countable, and every later layer's exam
   counts the earlier layers' markers to prove nothing was clobbered.
4. **Examined at build time.** Each `Dockerfile.tX` ends with a `verify_*.py` that runs
   inside the build. Those exams are **CPU-only**: a `docker build` has no GPU, and the
   CUDA extensions link `libcuda.so.1`, so nothing there may import them. They check
   markers, re-parse every edited file with `ast`, import the patched Python modules
   without a driver, and re-derive the arithmetic the patch depends on.

Section 8 is the counter-example that justifies rule 4's limits: the EP patch passed a
6/6 CPU unit test and still produced garbage on the GPU. A CPU exam proves a patch was
applied and is syntactically coherent. It does not prove the kernel is right.

---

## 5. HAREM-TP3-LIL — shape padding for TP=3

**Image:** `t3` · **Files:** [`patches/patch_tp3_lil.py`](../patches/patch_tp3_lil.py),
[`patches/verify_t3.py`](../patches/verify_t3.py),
[`patches/Dockerfile.t3`](../patches/Dockerfile.t3) · **Marker:** `HAREM-TP3-LIL`,
17 edits across 5 files.

### What it changes

| File | Function(s) | Change |
|---|---|---|
| `vllm/model_executor/parameter.py` | new `_harem_tp3_pad_then_narrow` + 4 call sites | When a TP shard runs past the end of the stored tensor, zero-pad first, then narrow ("pad-then-narrow") |
| `vllm/model_executor/model_loader/weight_utils.py` | `row_parallel_weight_loader`, `sharded_weight_loader` | Same pad, 2 sites |
| `vllm/model_executor/layers/vocab_parallel_embedding.py` | `__init__` | `padding_size` raised to `lcm(64, 3) = 192` |
| `vllm/model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py` | `a_log_weight_loader`, `_make_fused_conv1d_weight_loader` | Pad the KDA per-head vectors (A_log stored at 64, local param 22) and the fused conv1d group (stored 64×128, local 66×128/3) |
| `vllm/models/glm5next/nvidia/model.py` | shape setup + `_h3_pad_loaded` + 3 loader call sites + `kda_state_shape` | Heads 64 → **66**; BF16 shared expert 2048 → **2112** = 11 × lcm(64,3); head rounding for the KDA state shape |

Marker counts the exams assert: `parameter.py` 5, `weight_utils.py` 2,
`vocab_parallel_embedding.py` 1, `kimi_gdn_linear_attn.py` 2, `model.py` 7.

### Why

The four indivisible numbers from section 1. Two details are worth spelling out:

- **The vocab pad is done in `__init__`, not at the call site.** That way the same fix also
  covers the DFlash2 drafter, which has the same 154880 vocabulary. 154880 → 154944.
- **The KDA loaders are new here.** Our two earlier TP=3 overlay sets did not have them:
  this fork moved the KDA into a shared Kimi layer. Without the pad, rank 2 crashes while
  loading weights.

### What it deliberately does NOT do

- **No 22 → 32 kernel pad, no FlashInfer overlay.** b12x's sparse-MLA supports 22 local
  heads natively — it runs the `heads % 16` remainder as a separate single-block grid
  (`b12x/attention/_shared/mla/kernel.py:2843-2853`, `3194-3200`, `VALID_HPB`). The
  GLM-specific `native_glm_h8` fast path needs `heads == 8` *and* a non-NVFP4 scale format,
  so it is off for this checkpoint at any TP. (This reasoning is correct for the *hard
  stops*. It missed the prefill wrapper's own 8-alignment rule and the silently wrong
  22-head decode path — sections 7 and 8.)
- **No DSA-indexer change.** The indexer replicates the global head count and never asks
  about TP divisibility (`b12x/attention/dsa_indexer/paged.py:315-333`).
- **No `moe_intermediate_size` pad.** Routed experts stay 2048 wide and are distributed by
  expert parallelism. With EP on we measured no difference between 2112 and 2048 — the pad
  only silenced a `VllmConfig` check. `[measured-here, raw lost]`
- **The checkpoint's `config.json` is not modified on disk.** The `SHA256SUMS` must stay
  verifiable; the padded shape is supplied through `--hf-overrides` at launch.

### How to verify

Build-time: `verify_t3.py` runs inside the image. Beyond marker counting it unit-tests the
pad helper against the exact shards TP=3 needs — a 64-long A_log narrowed to rank 2's rows
44:66, a 4096×2048 shared-expert `down_proj` narrowed to 4096×704 with the pad edge in the
right place, and a shard that already fits being returned untouched. It then asserts
`pad_vocab_size(154880, 192) == 154944`, that `config.moe_intermediate_size` is **not**
assigned anywhere in `model.py`, and that the fork's own head guard still exists (if that
guard ever vanishes upstream, the head pad may no longer be needed and you should know).

Run-time, in the engine log:

```
[EP Rank 0/3] Local/global number of experts: 96/288
```

That line (`expert_map_manager.py:245`) is the proof that expert parallelism took over the
288 routed experts instead of tensor-slicing them.

**Cost.** Speed: the padded heads do arithmetic that is thrown away — 66 heads instead of
64, so ~3 % of attention work is wasted, and 2112 instead of 2048 on the shared expert.
Memory: 154944 − 154880 = 64 extra vocabulary rows and 64 extra shared-expert columns per
layer, a few hundred MB across the model. Quality: none — padded rows are zero and are
sliced away. `[estimate]` for the 3 %, which we did not isolate.

**Authorship.** Written by us for this recipe (Apache-2.0; a credit is appreciated). The
*idea* is not ours: our earlier NVFP4-era work took it from a community approach that pads
the checkpoint's `config.json` on disk before launch. We looked for the original script
while preparing this repository and **could not find it** in the repositories we had noted,
so we credit it as an idea rather than as a source (see [../CREDITS.md](../CREDITS.md)).
What is ours is doing the equivalent inside the engine, so the checkpoint stays
hash-verifiable.

---

## 6. HAREM-TP3-LIL-C4 — uniform KV page rounding

**Image:** `t3d` · **Files:** [`patches/patch_c4_lil.py`](../patches/patch_c4_lil.py),
[`patches/verify_t3d.py`](../patches/verify_t3d.py),
[`patches/Dockerfile.t3d`](../patches/Dockerfile.t3d), full investigation in
[`patches/fix-C4.md`](../patches/fix-C4.md) · **Marker:** `HAREM-TP3-LIL-C4`, 1 edit,
1 file.

**Say this plainly, because it is the honest part: the fix already existed in the fork.**
`vllm/v1/core/kv_cache_utils.py:1310-1319` already rounds the pool stride up to `64*132`,
with the fork's own comment describing exactly our failure. It was gated behind an
unrelated experiment flag:

```
os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None
and contains_glm5_next_mla
```

Our patch **drops the env-var half of the condition and keeps the model half**. The
mechanism, the constant `64*132`, and the comment are the fork's. What is ours is the
diagnosis that the invariant is unconditional, and the evidence for it.

### The symptom

`t3` cleared every TP=3 shape blocker, allocated the KV cache, and then died:

```
vllm/models/glm5next/nvidia/pooled_indexer.py:314-317
ValueError: GLM MLA parent-page stride must be an exact number of C4 pages
```

### Root cause

One temporary print before that line closed it (`patches/probe/patch_c4_probe.py` inserts
exactly that print; `patches/Dockerfile.probe` builds a throwaway image with it):

```
shape=(1505, 3328, 528)  stride=(25559040, 528, 1)
semantic=1757184  tail=109824  need=1867008  index_page_bytes=8448
25559040 % 8448 = 3840
```

The chain, in `_get_kv_cache_bytes_per_block` and downstream:

1. `kv_cache_utils.py:1297-1303` — `bytes_per_block` is the **maximum across groups** of
   that group's summed layer pages. The **DFlash draft group wins**: 5 layers × 3328 tokens
   × 1536 B/token = **25,559,040** (drafter bf16 KV at TP=3 = 3 local KV heads × 2 × 128 ×
   2 B).
2. `:1387-1388` — that value becomes `interleaved_block_stride` (the layout is
   block-outermost).
3. `:1420-1424` — it enters `compute_layout_strides` as a fixed stride.
4. `kv_cache_interface.py:326, 352-358` — the MLA view is built with that stride.
5. `pooled_indexer.py:314` — rejects it.

The arithmetic is the whole story: `8448 = 2^8 · 3 · 11`, while `3328 = 2^8 · 13` and the
drafter's `7680 = 2^9 · 15` carry no factor 11. The MLA page itself was always clean
(`3328·528 = 208·8448`, page `1,867,008 = 221·8448`); the fault sits one level up, in the
pool stride, and the *draft* group is what put it there. At TP=2 and TP=4 the drafter's
per-token bytes and the chosen block differ — which is why the lab, running TP=2 and TP=4,
never saw this.

### Effect

`25,559,040 → 25,563,648` = **+4,608 B per block, +0.018 %**. `25,563,648 = 8448 × 3026`,
remainder 0, so `parent_stride_pages = 3026` is exact and `_index_cache_view`'s virtual
page id `p*3026 + s` stays a bijection consistent with the b12x indexer op.

### Rejected alternatives

- **Setting `VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE`.** It would also flip
  `kv_cache_utils.py:1931-1932` into the split-cache grouping path — a far larger behaviour
  change than a round-up. `[not tested]`
- **`--block-size 5632`.** Pads the KDA/mamba page by ~79 %. `[estimate]`

### How to verify

`verify_t3d.py` asserts the marker, re-checks all five `HAREM-TP3-LIL` marker counts from
`t3`, pulls the **live** function source with `inspect.getsource` (so a patched-file-but-
stale-import situation is caught), and then reproduces the measured arithmetic as a unit
test: 25,559,040 → remainder 3840 → `round_up` → 25,563,648, remainder 0, and re-derives
`pooled_indexer`'s own gate.

**Cost.** Memory: 6.9 MB total at 1505 blocks — below the resolution of the KV-size log
line. Speed: none. Quality: none.

**Authorship.** Diagnosis and the un-gating are ours. **The fix mechanism is the fork's
own code** (`local-inference-lab/vllm`, Apache-2.0); we changed one condition.

---

## 7. HAREM-B12X-PREFILL-HPAD — prefill at 22 heads

**Image:** `t3e` · **Files:**
[`patches/hpad/patch_hpad_b12x.py`](../patches/hpad/patch_hpad_b12x.py),
[`patches/hpad/verify_hpad.py`](../patches/hpad/verify_hpad.py),
[`patches/Dockerfile.t3e`](../patches/Dockerfile.t3e) · **Marker:**
`HAREM-B12X-PREFILL-HPAD`, 1 anchored edit + 1 helper, 1 file.

**Target:** `b12x/attention/_shared/mla/prefill.py`, the `run_unified_prefill` wrapper;
new helper `_harem_hpad_heads`.

**Symptom:** b12x sparse-MLA prefill refuses with *"heads divisible by 8, got 22"*.

**Root cause:** TP=3 leaves 22 local heads. The MG kernel supports a 16 + 8 partition
(`VALID_HPB = 8`); it does not support a 6-wide tail. Decode already accepted 22 — which
at the time we read as "decode is fine". See section 8.

**Fix:** in the prefill wrapper, zero-pad the query heads 22 → **24**, run the supported
16 + 8 partition, then copy the first 22 rows of `O` and `LSE` back. `attn_sink` is padded
the same way. The padded heads have zero queries, so their outputs are finite and unused.

**Verify:** `verify_hpad.py` asserts the marker appears exactly twice, then `exec`s the two
head-partition functions straight out of the patched file and checks them:
`_harem_hpad_heads` maps 22→24, 8→8, 16→16, 12→16, 24→24, 6→8; `_mg_head_partitions(24,16)`
returns `((1,16,0), (1,8,16))` while `_mg_head_partitions(22,16)` returns `()`. It also
re-counts the `t3` and C4 markers.

**Effect.** `t3e` was the **first image that started successfully** (3 Sep 2026, 01:17).
Startup ~4 min, KV pool 4,371,014 tokens, correctness probe 10/10, code exam 12/12 on a
repeat run, 7k prefill **1,557 tok/s**. `[measured-here]` Settings: `t3e`, TP=3 + EP +
marlin MoE, NVFP4 `modelopt_mixed`, KV dtype auto, DFlash2 k=7, `--enforce-eager`,
`gpu-memory-utilization 0.85`, thinking on / reasoning effort `low`, temperature 0.

**Cost.** Speed: **+9 % prefill attention work** plus two small copies. `[estimate]`
Memory: none (no persistent buffers). Quality: none — decode, KV layout and weights are
untouched.

**Authorship.** Written by us for this recipe (Apache-2.0; a credit is appreciated).

---

## 8. HAREM-B12X-QPAD — the decode fix, and the root cause of the night

**Image:** `t10` · **Files:**
[`patches/t10/patch_qpad_fork.py`](../patches/t10/patch_qpad_fork.py),
[`patches/t10/verify_t10.py`](../patches/t10/verify_t10.py),
[`patches/Dockerfile.t10`](../patches/Dockerfile.t10) · **Marker:** `HAREM-B12X-QPAD`,
4 anchored edits (5 marker occurrences), 1 file.

**Target:** `vllm/v1/attention/backends/mla/b12x_mla_sparse.py` — the backend that plans
and runs b12x sparse-MLA **decode and extend**. New attributes `_harem_real_heads`,
`_harem_qpad`; new method `_harem_qpad_specs`.

### Root cause

The b12x MLA **decode** kernel computes **silently wrong results at 22 heads**.

22 is `16 + 6`. The kernel is validated on 8-aligned head counts — TP=2 gives 32, TP=4
gives 16, and those are the configurations the lab runs. It never raised an error on our
shape; it ran, and returned numbers that were subtly wrong. Nothing in the log said so.

What it looked like from the outside, before the patch: the code exam scored anywhere
between 7/12 and 10/12 depending on the run, speculative acceptance sat at 44–53 %, and
repeated runs at temperature 0 gave different output.

The experiment that isolated it (`t9`): **turn speculation off**. With no drafter, the same
image scored 10/10 on the correctness probe and **12/12 on the code exam, four times, with
two rounds bit-identical**. So the model and the weights were clean and the defect lived in
the speculative decode path — which is where the extend/decode plans run, at 22 heads.

### What the patch does

The same trick as the prefill patch, one layer up and with more care because decode runs
inside CUDA graphs:

1. Keep the real head count (`self._harem_real_heads = self._input_num_heads`) and round
   the *planned* head count up to a multiple of 8: `((n + 7) // 8) * 8`, so 22 → 24.
2. Append three **staging buffer specs** to both workspace spec lists — query, output and
   LSE, all at the real head count. Because they are declared as workspace specs, they are
   reserved up front.
3. On the run path: build the query into the staging buffer, copy it into the padded query
   buffer, zero heads 22–23.
4. After the kernel returns, copy the first 22 rows of `O` and `LSE` back into the staging
   buffers and return those.

**There is no allocation at run time.** That is the point of the staging buffers, and it is
what makes the patch CUDA-graph safe — which matters, because full CUDA graph capture is
the production setting and it is worth +22 % on single-stream decode.

### Effect

Settings for both arms: TP=3 + EP + marlin MoE, `local-inference-lab/GLM-5.3-Flash-NVFP4`
(`modelopt_mixed`), KV dtype auto, DFlash2 draft k=7, CUDA graphs on + AOT,
`--mamba-cache-mode align`, `gpu-memory-utilization 0.85`, no KV pin, temperature 0,
thinking on / reasoning effort `low`, `max_tokens` 256, realistic prompt set (12 short
English code prompts), 3 Sep 2026. `[measured-here]`

| Measure | t7 (before QPAD) | t10 (after QPAD) |
|---|---|---|
| Code exam | 7–10/12, varying between runs | **12/12, three times** (gate + 2 repeats) |
| Correctness probe | — | 10/10 |
| Speculative acceptance | 44–53 % | **62–65 %** (5.4–5.6 tokens/step) |
| Decode, 1 stream | 48.3 / 48.3 tok/s | **56.9 / 56.5 tok/s** |
| Decode, 8 concurrent, total | 115–118 tok/s | **145.8 tok/s** |
| Prefill, 7k prompt, uncached | — | 1,585 tok/s |

One patch fixed quality, acceptance and speed together — which is what you would expect
when the defect was wrong numbers rather than a slow path: a drafter proposing against a
wrong verifier gets rejected more often.

**Verify:** `verify_t10.py` asserts 5 marker occurrences, that the rounding expression is
present verbatim, that `_harem_qpad_specs()` is appended to **both** spec lists
(`count == 2`) and that the run path slices `workspaces[-3:]` exactly once; it re-checks
the rounding for 22→24, 16→16, 32→32, 21→24, byte-compiles the file, and re-counts the
`t3`, C4 and HPAD markers.

**Cost.** Speed: ~9 % more attention arithmetic per decode step (24 planned heads instead
of 22) plus three copies — and it still came out ~18 % *faster* on single-stream decode,
because acceptance rose from 44–53 % to 62–65 %.
Memory: three staging buffers sized `max_tokens × 22 × head_dim`, reserved from the
workspace, not the KV pool. Quality: this is the patch that bought 12/12.

**Authorship.** Diagnosis and patch written by us for this recipe (Apache-2.0; a credit is
appreciated).

**The lesson worth stealing:** when a padded shape is fed to someone else's kernel, do not
assume that "it did not raise" means "it is correct". The kernel that raised (prefill) cost
us an hour. The kernel that stayed quiet (decode) cost us the night.

---

## 9. HAREM-B12X-MOE-EP and HAREM-B12X-MOE-EP-ROUTE — tried, closed

**Images:** `t4`, `t4b` (branched off `t3e`) · **Files:**
[`patches/ep-patch/patch_b12x_moe_ep.py`](../patches/ep-patch/patch_b12x_moe_ep.py),
[`patches/ep-patch/verify_t4.py`](../patches/ep-patch/verify_t4.py),
[`patches/ep-patch/test_b12x_moe_ep_cpu.py`](../patches/ep-patch/test_b12x_moe_ep_cpu.py),
[`patches/Dockerfile.t4`](../patches/Dockerfile.t4),
[`patches/t4b/patch_moe_route_ep.py`](../patches/t4b/patch_moe_route_ep.py),
[`patches/t4b/verify_t4b.py`](../patches/t4b/verify_t4b.py),
[`patches/Dockerfile.t4b`](../patches/Dockerfile.t4b) · **Markers:** `HAREM-B12X-MOE-EP`
(12 anchors), `HAREM-B12X-MOE-EP-ROUTE`.

**This is documented as an attempt, not as a recommendation. Production uses `marlin` for
the MoE, not b12x.** These images are here because the failure is informative and because
someone will otherwise try the same thing.

### Why we wanted it

The fork wires b12x's MoE in TP-only mode and refuses expert maps outright:

```
vllm/model_executor/layers/fused_moe/b12x.py:486-495   _supports_parallel_config wants use_ep == False
                                            :504-505   supports_expert_map() -> False
                                            :764-765   raise "b12x TP MoE does not support expert maps"
```

`flashinfer_b12x` refuses them too (`experts/flashinfer_b12x_moe.py:199-202`), and
`cutlass` wants `ep_size == 1` (`experts/cutlass_moe.py:736-739`). **Only `marlin` accepts
EP on this hardware** (`experts/marlin_moe.py:642-648`) — and marlin is weight-only:
`oracle/nvfp4.py:448-467` sets `a13_scale`/`a2_scale` to `None` and `:539-550` hands over a
W4A16 quant config, so the checkpoint's W4A4 **activation scales are silently dropped**
(FP4 weights, BF16 activations). We have never measured what that costs in quality.
`[not tested]` That is a known, unmeasured price of the production configuration, and
getting b12x's own MoE to accept EP was the way to find out.

### What the patches do

**t4** (`vllm/model_executor/layers/fused_moe/b12x.py`): accept an expert map, but only on
W4A16 plans — b12x gates both of its map-aware entry points to W4A16
(`b12x/moe/fused_moe/_impl.py:2862`, `b12x/moe/ep_moe/_impl.py:194`). So with EP on, the
recipe switches to W4A16: same FP4 weights, same `modelopt_nvfp4` source format, BF16
activations. `fused_moe` was chosen over `ep_moe` because both end in the same kernel
(`w4a16/kernel.py:run_w4a16_moe`) and `fused_moe` keeps the fork's plan cache, warmup and
CUDA-graph discipline. `Caps.route_num_experts = global_num_experts` while the weights stay
local (`weight_E = local_num_experts`); `layer.expert_map` is handed over as
`route_expert_map`. With EP off, every call is byte-identical to the original.

**t4b** (`b12x/moe/fused_moe/_impl.py`, helpers `_harem_ep_route_mode`,
`_harem_mapped_zero_fc2`): with a map bound, the planner still planned W4A16
`route_mode='direct'`, but the small-M direct micro kernel has no expert-map contract
(`_small_m_direct_supported` requires `expert_map is None`), so `run_w4a16_moe` raised
*"planned W4A16 direct routing is unsupported for this launch shape"*. The patch degrades
`'direct'` to `'auto'` when a `route_expert_map` is bound, and selects the zeroed-FC2
route-pack variant.

### Result — CLOSED

`t4b` started (3 Sep 2026, 02:31–02:52, `--enforce-eager`) and produced **corrupt output:
correctness probe 7/10, code exam 0/12**. KV pool 4.18M tokens, prefill 1,221 tok/s, cold
single-stream TTFT 30.8 s (JIT compiles). `[measured-here]`

**b12x MoE + EP is off. Production is `marlin` + EP.**

Two things to carry away:

- **The CPU unit test passed 6/6 and proved nothing.** `test_b12x_moe_ep_cpu.py` checks the
  id-remapping and mask logic (288 global → 96 local, `ep_size` 3) and the source
  contracts, all without a GPU. It was not a numerical test against a reference. If anyone
  reopens this, a **numerical unit test against a reference implementation is mandatory**
  before a live run.
- **One claim in our own build notes was wrong.** `patches/NOTES.md` §07 says "use
  `flashinfer_b12x` if you need EP". It does not accept expert maps either. The note is
  left in place with the correction attached, because a recipe that quietly edits its own
  mistakes is not worth reading.

A side finding that is useful regardless: with EP on, the MoE's TP degree becomes 1, so the
`moe_intermediate_size` 2048 → 2112 pad is unnecessary. That is why section 5 does not do
it.

**Authorship.** Both patches written by us for this recipe (Apache-2.0). They are published
as a record of a dead end.

---

## 10. Not in the image: the drafter's load format (fix-A)

Worth knowing here because it looks like an image problem and is not.
[`patches/fix-A.md`](../patches/fix-A.md) documents it in full.

On `t3`'s first live start, rank 0 **segfaulted** while loading weights
(`cuMemcpyDtoDAsync_v2` → `cudaMemcpyAsync` → `at::native::copy_device_to_device`). Cause:
the drafter inherits the target's `--load-format instanttensor` with
`{"instanttensor_copy": false}`, which hands out borrowed ring-buffer views; our
pad-then-narrow `F.pad`/`copy_` then reads device-to-device out of a borrowed buffer.

The fix is one launcher flag, not an image change — give the drafter its own load config:

```
"draft_load_config":{"load_format":"safetensors"}
```

The target stays on instanttensor. Measured after the fix: drafter loads in **5.71 s**,
target in 57–58 s, segfault gone. `[measured-here]` The flag lives in the serving
configuration ([03-launch-and-flags.md](03-launch-and-flags.md)).

---

## 11. Verification — how you (or your agent) confirm the image is right

Do all four. They are ordered cheapest first, and each one catches something the previous
one cannot.

### 11.1 The image is what you think it is

```
docker image inspect harem/glm53-lil:t10 --format '{{json .Config.Labels}}'
```

Expect `harem.recipe`, `harem.tp="3"`, `harem.patch.marker`, `harem.vllm.commit`,
`harem.b12x.commit`. Then, on **all three nodes**:

```
docker image inspect harem/glm53-lil:t10 --format '{{.Id}}'
```

The three IDs must be identical. If they are not, stop and re-ship — a mismatched worker
serves wrong tokens without complaining.

### 11.2 The patches are present, in the running image

Every marker, counted, in one command:

```
docker run --rm --entrypoint python3 harem/glm53-lil:t10 -c "from pathlib import Path; v=Path('/usr/local/lib/python3.12/dist-packages/vllm'); b=Path('/usr/local/lib/python3.12/dist-packages/b12x'); [print(f'{n}: {p.read_text().count(m)}') for n,p,m in [('TP3 parameter.py', v/'model_executor/parameter.py','HAREM-TP3-LIL'),('TP3 weight_utils.py', v/'model_executor/model_loader/weight_utils.py','HAREM-TP3-LIL'),('TP3 vocab_parallel_embedding.py', v/'model_executor/layers/vocab_parallel_embedding.py','HAREM-TP3-LIL'),('TP3 kimi_gdn_linear_attn.py', v/'model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py','HAREM-TP3-LIL'),('TP3 model.py', v/'models/glm5next/nvidia/model.py','HAREM-TP3-LIL'),('C4 kv_cache_utils.py', v/'v1/core/kv_cache_utils.py','HAREM-TP3-LIL-C4'),('HPAD prefill.py', b/'attention/_shared/mla/prefill.py','HAREM-B12X-PREFILL-HPAD'),('QPAD b12x_mla_sparse.py', v/'v1/attention/backends/mla/b12x_mla_sparse.py','HAREM-B12X-QPAD')]]"
```

Expected counts: **5, 2, 1, 2, 7, 1, 2, 5**. Any other number means a layer is missing or a
patch applied twice.

The `verify_*.py` exams are already inside the image and can be re-run directly:

```
docker run --rm --entrypoint python3 harem/glm53-lil:t10 /opt/harem/t10/verify_t10.py
```

`verify_t10.py` re-checks every earlier layer as well, so it is the single best one-shot
answer to "is this the production image". The others are at `/opt/harem/verify_t3.py`,
`/opt/harem/verify_t3d.py` and `/opt/harem/hpad/verify_hpad.py`.

### 11.3 The GPU side actually loads

```
cd ~/glm53-build && ./verify-t2-gpu.sh harem/glm53-lil:t10
```

No model, no server, no KV cache — it imports the CUDA extensions, checks the live operator
table against the fork's `csrc` (`situ_and_mul_quant` present, `situ_and_mul` carrying
`valid_rows`, the removed DeepSeek-V4 op absent), and JIT-compiles one 128×128 b12x nvfp4
kernel for real. Safe to run while nothing else is on the GPU. Two traps are baked into the
script and worth knowing if you write your own: `docker run` without `-i` never delivers a
heredoc to python and **exits 0 silently**, and `dir(torch.ops._C)` is lazy — it reports 7
entries no matter what is registered, so probe by name and cross-check with
`torch._C._jit_get_all_schemas()`.

### 11.4 Log lines to look for on the first live start

| Line | What it proves |
|---|---|
| `vllm 0.1.dev0+lil.jovian.9c4dd0548` | the fork build, not the base image's vLLM |
| `Glm5NextForConditionalGeneration` resolved, no `64 % 3` assert, no `num_heads must be divisible` ValueError | HAREM-TP3-LIL is doing its job |
| `[EP Rank 0/3] ... Local/global number of experts: 96/288` (`expert_map_manager.py:245`) | expert parallelism took the 288 routed experts |
| `modelopt_mixed` recognising NVFP4, W4A16_NVFP4 and MXFP8 | the mixed-precision checkpoint parsed |
| `Setting attention block size to 3328 tokens`, then a `GPU KV cache size: N tokens` line with **no** `must be an exact number of C4 pages` ValueError | HAREM-TP3-LIL-C4 |
| no `heads divisible by 8, got 22` | HAREM-B12X-PREFILL-HPAD |
| `Loading safetensors using InstantTensor loader` for the target, then `Loading safetensors checkpoint shards` for the drafter — and **no second** InstantTensor line | fix-A (drafter load format) |

HAREM-B12X-QPAD has **no log line**. That is the whole point of it: the bug it fixes was
silent. The only way to confirm it is the quality gates below.

### 11.5 Quality gates — the real exam

Neither of these needs a benchmark harness; both take a couple of minutes and both must
pass before the image is trusted for anything.

```
python3 scripts/correctness-probe.py http://192.0.2.10:8000
```

Expect **10/10** and zero empty-content responses.

```
python3 scripts/code-exam.py http://192.0.2.10:8000
```

Expect **12/12**, at temperature 0, and expect it to be **repeatable** — run it three
times. This is the gate that caught HAREM-B12X-QPAD: before the patch it wandered between
7/12 and 10/12 across runs, and a score that moves at temperature 0 is itself the finding.
A single 12/12 proves less than three consecutive ones.

```
audit/run-audit.sh
```

The audit script walks 11.1 through 11.4 in one pass and prints a pass/fail table; use it
after any rebuild or reboot.

If the code exam is unstable but the correctness probe passes, suspect a padded shape
reaching a kernel that does not raise — that is exactly the QPAD signature, and the
isolating experiment is to turn speculative decoding off and re-run. If it goes to 12/12
with speculation off, the defect is in the speculative path, not in the weights.

---

## 12. Open problems in this layer

- **`instanttensor` is not version-pinned.** The Dockerfile installs `>=0.1.9` and we did
  not record which version we got. A build today and a build tomorrow are not guaranteed to
  be the same image. Pin it. `[not tested]`
- **The base image's provenance.** We do not know who publishes
  `vllm/vllm-openai:glm53-flash-arm64-cu130` or whether the tag will persist. Pull by
  digest; we have no fallback path documented for the day it disappears.
- **The quality cost of `marlin`'s W4A4 → W4A16 drop** is real and unmeasured, and section 9
  explains why we could not measure it (the only alternative path produced corrupt output).
  `[not tested]`
- **NCCL license text.** The image ships NCCL 2.29.7 from the base; we read the version from
  the engine log but never opened the bundled license file, so [../CREDITS.md](../CREDITS.md)
  records it as *license not confirmed*. `[not tested]`
