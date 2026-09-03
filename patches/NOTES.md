# Build notes — harem/glm53-lil:t2 … t3d

Working notes taken while the image chain was built (2–3 Sep 2026), translated
from the original Turkish. They stop at t3d; the t3e and t10 layers are written
up in [docs/02-image-build.md](../docs/02-image-build.md). Stack: the Local
Inference Lab vLLM fork `dev/jovian-judgement` + b12x + the `lil` launcher
(reference only).

Nothing in here is a conclusion on its own — each finding that survived is
restated with its evidence in `docs/02-image-build.md`.

## 00 — start
2026-09-02T22:25:05+03:00

## 01 — sources (2 Sep)
- GitHub's HTTP/2 endpoint answers 401 on this host/network;
  `git -c http.version=HTTP/1.1` works. Written into the local git config of the
  fork checkout, and set globally inside the image.
- `vllm-lil` = `local-inference-lab/vllm` @ `dev/jovian-judgement`,
  HEAD `9c4dd05487629eccb26d7166459867a3db9b099f`
- `b12x` HEAD `887607b26f952f7cc13b5ad4ef720627eced0486` (version 1.3.0, pure
  Python, JIT)
- `lil` HEAD `cb58d54`; Go 1.26.0 arm64 -> `goroot/`, binary -> `lil-bin`

## 02 — commit analysis (the build-path decision)
- Base image commit `487ecf187` = a `vllm-project/vllm` PR branch; it left main
  at `b908a21f` (2026-08-12).
- Fork `merge-base(upstream/main)` = `299ebd094` (2026-08-25 17:31). 75 commits
  are the fork's own.
- Those 75 commits touch NOTHING under `csrc/`, `cmake/` or `CMakeLists.txt`
  (only `setup.py`: b12x 1.2.6 -> 1.3.0).
- BUT the tree diff `487ecf187 -> 9c4dd0548` is 27 files, +4669/−152
  (`csrc`/`cmake`/`CMakeLists`/`setup.py`). `torch_bindings.cpp` changes the
  registered SCHEMAS: `+fused_gdn_decode_post_conv_mtp`, `+situ_and_mul_quant`,
  `valid_rows` added to the `situ_and_mul` signature,
  `-fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out`.
- DECISION: (b) FULL SOURCE BUILD. Prebuilt `.so` files cannot be reused.

## 03 — gaps in the base image (found during the build)
- `git` MISSING -> installed with apt (CMake FetchContent clones with git).
- `cmake` MISSING -> installed with pip. `nvcc` 13.0.88, `ninja`, `gcc` present.
  torch 2.13.0+cu130.
- `/usr/local/cuda/lib64/libnvrtc.so` (the unversioned symlink) MISSING ->
  `CUDA_nvrtc_LIBRARY=NOTFOUND`, so `spinloop`/`cumem_allocator`/`fs_io_C` fail
  to link. Symlink added.
- `rustc` MISSING; the base image's `_rust_tool_parser.abi3.so` was copied into
  the checkout so `setup.py` takes its `precompiled_build_rust` path.
- `instanttensor` MISSING -> installed with pip (needed for
  `--load-format instanttensor`).

## 04 — findings about `lil` (the lab's own launcher)
- `lil/internal/launcher/builder.go:145` errors when
  `attention_heads % tp != 0`. `configs/models/_bases.yaml`, glm base:
  `attention_heads: 64`.
- `lil/internal/launcher/checks.go:198-203` re-verifies against the
  checkpoint's `config.json`.
- `lil render GLM-5.3-Flash-NVFP4 --tp 3` output:
  *lil: model "GLM-5.3-Flash-NVFP4" has 64 attention heads, which is not
  divisible by TP=3* (for both the local and the spark topology).
- The `lil.yaml` in the HF catalogue (inside the model directory) uses the NEW
  schema (`serving`/`kernels`/`speculators`); the cloned `lil` binary reads the
  OLD flat schema. The values are the same.

## 05 — TP=3 evidence
- `b12x/attention/_shared/mla/kernel.py:2843-2853` + `3194-3200`: `VALID_HPB`.
  The kernel runs the `heads % 16` remainder as a SEPARATE single-block grid ->
  22 local heads are natively supported. No 22 -> 32 pad is needed (unlike the
  FlashInfer sm120 kernel).
- `b12x/attention/dsa_indexer/paged.py:315-333`
  `resolve_replicated_num_q_heads`: the indexer is TP-independent, it COPIES the
  global head count -> it never asks about TP divisibility.
  (`resolve_local_num_q_heads` is legacy; that one does have a `%` check at
  line 351.)
- `vllm-lil/vllm/models/glm5next/nvidia/model.py:760-762`
  `assert config.num_attention_heads % world_size == 0` -> 64 % 3 != 0, HARD
  STOP. Cleared by our 66-head `--hf-overrides` pad.
- Model shape: `num_hidden_layers` 45, `num_nextn_predict_layers` 1 (MTP = layer
  45), `num_attention_heads`/`num_key_value_heads`/`linear_num_heads` = 64,
  `vocab` 154880, `moe_intermediate_size` 2048, `n_shared_experts` 1,
  `intermediate_size` 12288 (12288/3 = 4096, fine).
- `vocab` 154880 % 3 = 2 -> the vocab pad must become a multiple of 192
  (154944); the fork does not do this.

## 06 — more TP=3 evidence
- `vllm-lil/vllm/models/glm5next/nvidia/attention.py:77-80` is a second hard
  check: `num_heads % tp_size` -> `ValueError`. Passes at 66.
- `b12x/attention/_shared/mla/compressed_api.py:29-56`, *"Select the 32-head
  final-output kernel for Spark one-wave decode"*: requires
  `compute_capability == (12,1)` AND `heads == 32` AND `swa_page_size == 64`.
  At TP=2, 64/2 = 32 selects that kernel. At TP=3, 22 DROPS OUT and the generic
  `VALID_HPB` path runs. So TP=3 WORKS but loses the decode kernel that was
  tuned for Spark. (Corrected in §08 — GLM does not take that path at all.)
- `b12x/attention/dsa_indexer/contiguous_kernel.py:94`
  `_PREFILL512_SUPPORTED_NUM_HEADS = (32, 64)` (the indexer is replicated, so it
  sees the global 64/66; at 66 the fast prefill512 path drops out and the
  fallback runs).
- `vllm-lil/vllm/models/glm5next/nvidia/model.py:920-922`: the spec layer (45) is
  ALWAYS skipped in the main model (*"skip spec decode layers for main model"*),
  whether the speculation method is dflash or mtp. The MXFP8 group is not
  loaded. Not a problem.
- DeepGEMM: `vllm/platforms/cuda.py:675-681` treats capability family 120 as
  supported -> ON by default on GB10; the lab does not disable it either. It is
  not on the hot path with the B12X backends.

## 07 — b12x MoE CLASHES with EP (critical)
- `vllm-lil/vllm/model_executor/layers/fused_moe/b12x.py:486-495`
  `_supports_parallel_config`: `use_ep` must be FALSE, `ep_size` must be 1.
- Same file, 504-505: `supports_expert_map() -> False`.
- Same file, 764-765: with an `expert_map` present,
  `ValueError("b12x TP MoE does not support expert maps")`.
  => `--moe-backend b12x` and `--enable-expert-parallel` DO NOT WORK together.
- The b12x library does have `moe.ep_moe`, but the fork never wired it into vLLM.
- Alternative when EP is required: `--moe-backend flashinfer_b12x`
  (around `vllm/config/kernel.py:250`; SM12x FlashInfer CuteDSL fused MoE).
  Only `b12x.py` and `hpc_moe.py` restrict `_supports_parallel_config`; the
  others allow EP.
  **This line is WRONG and was corrected later** — see section 9 of
  [docs/02-image-build.md](../docs/02-image-build.md):
  `experts/flashinfer_b12x_moe.py:199-202` refuses expert maps too. Only
  `marlin` accepts EP on this hardware.
- Conclusion at the time: for TP=3 either (a) pad `moe_intermediate_size`
  2048 -> 2112 and keep b12x MoE (no EP), or (b) EP plus a different MoE
  backend. Neither had been measured yet.

## 08 — one item in §06 CORRECTED
- The `compressed_api.py:29-56` "32-head Spark one-wave" gate is for DeepSeek-V4.
  GLM-5.3-Flash does NOT use that path:
  `vllm/models/glm5next/nvidia/attention.py:36` ->
  `vllm/v1/attention/backends/mla/b12x_mla_sparse.py` ->
  `vllm/utils/b12x.py:90-91` `get_b12x_sparse_mla()` = `b12x.attention.sparse_mla`
  (NOT the compressed variant). `compressed_sparse_mla` appears in the fork only
  in `vllm/models/deepseek_v4/nvidia/b12x.py`.
- On the `sparse_mla` side the GLM fast path is `kernel.py:3041-3049`
  `native_glm_h8`, which requires `heads == 8` (i.e. TP=8) AND
  `scale_format != NVFP4_E4M3`. Our checkpoint is NVFP4, so that path is CLOSED
  regardless of head count; the generic HPB=16 branch runs. The only extra cost
  at 22 heads is one additional single-block grid launch for the remainder (6).
  => On the b12x KERNEL side there is no TP=3-specific blocker or loss.
  (This held for decode and for prefill in the sense of "no hard stop"; the
  prefill wrapper's own 8-alignment requirement and the silently wrong 22-head
  decode path were both found later — HAREM-B12X-PREFILL-HPAD and
  HAREM-B12X-QPAD.)

## 09 — image build (t2) process notes
- Attempt 1: no `git` -> FAILED (2 min).
- Attempt 2: `CUDA_nvrtc_LIBRARY NOTFOUND` -> FAILED (cmake configure, after
  159 s).
- Attempt 3: with git + ccache + the `libnvrtc.so` symlink; the wheel build
  started at 22:38.
- Discovered during the build (all folded permanently into the Dockerfile):
  * In the STEP 6 exam `vllm._C_stable_libtorch` CANNOT BE IMPORTED: it needs
    `libcuda.so.1`, and a docker build has no GPU. The exam therefore checks the
    `.so` files exist and reads the schema strings inside them. The
    discriminators were VERIFIED against the base image:
      `situ_and_mul_quant(`  base: ABSENT  -> new: PRESENT
      `Tensor? valid_rows`   base: ABSENT  -> new: PRESENT
      `fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out(`
                             base: PRESENT -> new: ABSENT
  * `b12x.list_ops()` returns `OpMeta` objects; the name is built as
    `"group.name"`.
- The GPU-side exam lives in a separate file: `verify-t2-gpu.sh` (no model, no
  server; a real JIT compile through a 128x128 nvfp4 quantizer).
- The full `lil render` output is in `lil-render.txt` (not shipped here).

## 10 — result (2 Sep 23:28)
- `harem/glm53-lil:t2` READY. Image id `871d2ec3d736`, 47.6 GB.
- Attempt 4 succeeded. Attempt 3 died after 2358 s on the `deepgemm_C` target
  with *"fatal error: cusparse.h: No such file or directory"* — the runtime CUDA
  include tree is incomplete. Fix: the missing headers under
  `nvidia/cu13/include` are symlinked into `/usr/local/cuda/include`
  (Dockerfile STEP 1b).
- The GPU exam (`verify-t2-gpu.sh`) PASSED. Two traps were hit and written down:
  * without `-i`, `docker run` never delivers the heredoc to python and exits 0
    SILENTLY.
  * `dir(torch.ops._C)` is lazy: it does not report the registered op count (it
    shows 7). The right way is `getattr` probing plus
    `torch._C._jit_get_all_schemas()`.
  * The CuTe DSL `plan()` call needs a CUDA DRIVER context; without allocating a
    tensor and synchronizing first it raises `CUDA_ERROR_INVALID_CONTEXT` (201).

## 11 — t3 = t2 + TP=3 (3 Sep)
Goal: `local-inference-lab/GLM-5.3-Flash-NVFP4` at TP=3 + expert parallelism on
three GB10 nodes. Files: `Dockerfile.t3`, `patch_tp3_lil.py`, `verify_t3.py`,
`Dockerfile.t3.dockerignore` (context 742 MB -> 2 files), `dagit.sh`.

### 11.1 — source verification (BEFORE any patch was written)
- `model.py:760-762` assert (64 % 3) and `attention.py:77-80` `ValueError`:
  CONFIRMED, both pass with `--hf-overrides` 66; no separate patch needed.
- `vocab_parallel_embedding.py:246` ACCEPTS `padding_size` as a PARAMETER.
  Raising it to `lcm(64, tp) = 192` inside `__init__` is BETTER than at the call
  site: the same fix then also covers the DFlash2 drafter, which has the same
  154880 vocab.
- `shared_experts` is PURE BF16 in the checkpoint (`.weight`, no scale, in the
  non-expert shard). `moe_intermediate_size` 2048 -> 2112 = 11 * lcm(64,3). The
  alternative — replicating it with `disable_tp` — is WRONG: the all-reduce after
  the fused MoE would count the shared expert three times.
- `linear_attn_config.num_heads` is read ONLY while the config is being built
  (`transformers_utils/configs/glm5_next.py:104-107`) -> `--hf-overrides` does
  NOT reach it. That is why the in-model head pad stays.
- FORK-SPECIFIC, absent from both earlier patch sets: the KDA now lives in a
  shared Kimi layer. `mamba/gdn/kimi_gdn_linear_attn.py:198-219`
  `a_log_weight_loader` (A_log stored at 64, local param 22) and `:223-243`
  `_make_fused_conv1d_weight_loader` (conv1d group stored at 64*128, local
  66*128/3). Both blow up on rank 2 without a pad.
- `dt_bias` uses `sharded_weight_loader(0)` -> covered by the `weight_utils`
  patch.
- MoE backend: b12x REFUSES EP (`fused_moe/b12x.py:486-495, 504-505, 764-765`),
  so does `flashinfer_b12x` (`experts/flashinfer_b12x_moe.py:199-202`), and
  `cutlass` wants `ep_size == 1` (`experts/cutlass_moe.py:736-739`). `marlin`
  ACCEPTS EP (`experts/marlin_moe.py:642-648`). BUT marlin is WEIGHT-ONLY:
  `oracle/nvfp4.py:448-467` sets `a13_scale`/`a2_scale` to `None` and
  `:539-550` hands over a w4a16 quant config -> the checkpoint's W4A4 activation
  scales are SILENTLY DROPPED. # NOT MEASURED
- `flashinfer_cutlass` is the only backend in the source that provides EP + real
  W4A4 + SM12x together (`experts/flashinfer_cutlass_moe.py:128-139, 195-200`),
  but `has_flashinfer_cutlass_fused_moe()` depends on a runtime condition.
  NOT TRIED.
- `SpeculativeConfig` accepts `draft_tensor_parallel_size`, `kv_cache_dtype`,
  `attention_backend` and `moe_backend` for dflash (`speculative.py:394, 406,
  411, 415`). Draft TP must be 1 or the target TP; anything else errors
  (`speculative.py:1683`).

### 11.2 — the patches (marker HAREM-TP3-LIL, 17 edits, 5 files)
Every edit asserts its anchor occurs EXACTLY ONCE and is a no-op on re-run.
1. `model_executor/parameter.py` (+1 helper, 4 calls):
   `_harem_tp3_pad_then_narrow` — when the TP pad runs past the stored size,
   zero-fill first, then narrow.
2. `model_executor/model_loader/weight_utils.py` (2): the same pad in
   `row_parallel_weight_loader` and `sharded_weight_loader`.
3. `model_executor/layers/vocab_parallel_embedding.py` (1): `padding_size` ->
   `lcm = 192`.
4. `model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py` (2): A_log + fused
   conv1d.
5. `models/glm5next/nvidia/model.py` (7): head pad 64 -> 66 (it does NOT touch
   `moe_intermediate_size`), shared expert 2048 -> 2112, `_h3_pad_loaded` plus 3
   loader call sites, `kda_state_shape` head rounding.

### 11.3 — deliberately NOT done
- No 22 -> 32 kernel pad, no FlashInfer overlay (see §05 and §08).
- Routed experts stay 2048 and go through EP. Measured: with EP on there is NO
  difference between 2112 and 2048; the pad only silenced a `VllmConfig` check.
- The target checkpoint's `config.json` is NOT MODIFIED ON DISK (there is a
  `SHA256SUMS`). The drafter's `config.json` is already 36/9 on all three nodes
  (from the EXL3 line; `.orig` is 32/8); at draft TP=1 that works correctly
  thanks to the `parameter.py` pad. # NOT MEASURED

### 11.4 — outcome
- Attempt 1: a bug in my own exam — `_h3_pad_loaded` occurs 4 times, not 3,
  because the definition counts too. The exam was fixed, not the patch.
- Attempt 2 PASSED. Rebuilt with `--provenance=false --sbom=false` so the image
  identity stays a plain image and not a manifest list.
- Launcher `~/glm3x/scripts/start-lil.sh` + `~/glm3x/.env.lil-t3` on all three
  nodes. `DRY_RUN=1` prints the command without running it. The rejection paths
  were exercised: fabric address, `ENABLE_EP=0`, `MOE_BACKEND=b12x` + EP,
  missing mount.
- ENGINE NOT YET STARTED (by design for that step).

  === APPENDIX: FIRST LIVE TP=3 ATTEMPT (3 Sep 00:08-00:12) ===
  Observed read-only; the run was not interfered with. Useful either way:

  POINTS PASSED (the patch's exam):
    - `Glm5NextForConditionalGeneration` resolved; the 64 % 3 assert and the
      `attention.py` `ValueError` did NOT fire.
    - The drafter (`DFlash2DraftModel`) loaded; `max_model_len` 1048576 ->
      1000000.
    - `modelopt_mixed` recognised all three groups: NVFP4, W4A16_NVFP4, MXFP8.
    - THE EP EVIDENCE LINE APPEARED: `expert_map_manager.py:245 [EP Rank 0/3]
      "Local/global number of experts: 96/288"`, layout linear.
    - The instanttensor loader ran (482M chunks at 4.24 GB/s).

  WHERE IT DIED:
    SEGFAULT on rank 0 while loading weights: `cuMemcpyDtoDAsync_v2` ->
    `cudaMemcpyAsync` -> `at::native::copy_device_to_device` ->
    `at::native::copy_` (Python side `THPVariable_copy_`). Immediately before
    it: *"NCCL WARN lib wrapper not initialized"* (ibvwrap). EngineCore shut
    down with *"WorkerProc initialization failed"*.

  Two rival hypotheses, NEITHER measured at the time:
    (a) An interaction between the pad and instanttensor.
        `--load-format instanttensor` + `{"instanttensor_copy":false}` maps the
        tensor WITHOUT COPYING; our `F.pad`/`copy_` reads D2D from that mapped
        buffer. Test: rerun the same arm with `{"instanttensor_copy":true}`, or
        with `--load-format auto`.
    (b) Nothing to do with the patch (lil stack / mesh plugin / ibvwrap). Test:
        start the same launcher at TP=1 or TP=2 — then no pad is triggered.
    (b) first, because it is cheap and leaves the patch entirely out of it.
  RESOLVED: it was (a). See `fix-A.md` — the drafter gets its own load format.

## 12 — t3d = t3 + the C4 pool-stride fix (3 Sep)
- t3 cleared ALL the TP=3 shape blockers and reached KV allocation, where it
  stopped: `pooled_indexer.py:314-317` *"GLM MLA parent-page stride must be an
  exact number of C4 pages"*.
- MEASURED (rank 0, temporary print): `stride(0)=25,559,040`, `rem=3840`,
  `block=3328`, `page=1,867,008`.
- Chain: `kv_cache_utils.py:1297-1303` `bytes_per_block` = max over groups of
  (sum of that group's layer pages) -> the DFlash DRAFT group wins: 5 layers x
  3328 tokens x 1536 B = 25,559,040 (drafter bf16 KV at TP=3 = 3 local KV heads
  x 2 x 128 x 2 B). `:1387-1388` that value becomes `interleaved_block_stride`
  (BLHNC is block-outermost), `:1420-1424` it enters `compute_layout_strides` as
  a fixed stride, `kv_cache_interface.py:326,352-358` builds the MLA view with
  it.
- Arithmetic: `8448 = 2^8*3*11`; `3328 = 2^8*13`; `7680 = 2^9*15` -> no factor
  11 anywhere. The MLA page was ALREADY clean (`3328*528 = 208*8448`,
  `3328*33 = 13*8448`, page `= 221*8448`); the fault is one level UP, in the
  pool stride. At TP=2/4 the drafter's per-token bytes and the chosen block
  differ, which is why the lab never saw it.
- THE FIX ALREADY EXISTED IN THE FORK, gated behind the wrong flag:
  `kv_cache_utils.py:1310-1319` rounds `bytes_per_block` up to `64*132`, but its
  condition is
  `os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None AND
  contains_glm5_next_mla`. `HAREM-TP3-LIL-C4` drops the env-var half and keeps
  the model half. 1 anchor, 1 file. 25,559,040 -> 25,563,648
  (+4,608 B/block, +0.018%), `25,563,648 = 8448 x 3026`.
- Rejected: setting `VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE` (it also flips
  `:1931-1932` into the split-cache grouping path); `--block-size 5632` (pads
  the KDA/mamba page by ~79%).
- `fix-A` is NOT in the image, it lives PERMANENTLY in the launcher:
  `"draft_load_config":{"load_format":"safetensors"}`. The drafter now logs
  *"Loading safetensors checkpoint shards"*, 5.71 s; the target stays on
  instanttensor at 58 s.
- The CPU-only exam PASSED; it reproduces the measured arithmetic as a unit test.
- ENGINE AGAIN NOT STARTED.
