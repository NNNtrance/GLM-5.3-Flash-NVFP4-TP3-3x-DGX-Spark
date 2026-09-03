# fix-C4 — "GLM MLA parent-page stride must be an exact number of C4 pages"

Failure: `vllm/models/glm5next/nvidia/pooled_indexer.py:314-317`, run of 00:20,
after `GPU KV cache size: 4,368,115 tokens`. NOTHING APPLIED — analysis only.

## 1. Constants (verified)
| symbol | value | source |
|---|---|---|
| `_POOL_SIZE` | 4 | pooled_indexer.py:44 |
| `_INDEX_PAGE_SIZE` | 64 | pooled_indexer.py:49 |
| `_INDEX_CACHE_WIDTH` | 132 | pooled_indexer.py:48 |
| `_INDEX_PAGE_BYTES` | 64*132 = **8448 = 256 x 33** | pooled_indexer.py:50 |
| `_MLA_RECORD_BYTES` | 528 | pooled_indexer.py:51 |
| index tail per token | 132//4 = **33** | b12x_mla_sparse.py:55, used at :450 |
| `attn_page_size_1_token` | 528 + 33 = **561** | interface.py:905-914 via `customize_spec` |
| B12X kernel block sizes | `[64]` and `[MultipleOf(64)]` | b12x_mla_sparse.py:463-464, 537-540 |
| pooled-indexer block rule | `block_size % (4*64) == 0` -> multiple of 256 | pooled_indexer.py:75-79 |
| `mamba_cache_mode` legal | `all` / `align` / `none` (default `none`) | config/cache.py:70,188 |

## 2. What actually happened in the run
    interface.py:974  Setting attention block size to 3328 tokens ...
    interface.py:998  Padding mamba page size by 5.79% ...
Our `--block-size 256` is only a FLOOR. `kernel_block_alignment_size = max(min(64), 256)` then
`max(...,128)` for MLA = **256** (interface.py:939-951); with `mamba_cache_mode=align` the else
branch runs `attn_block_size = 256 * cdiv(mamba_page, 256*561)` (interface.py:970-974) and got
**3328 = 13 x 256**. Then `mamba_page_size_padded = attn_page_size` (interface.py:995).

## 3. The arithmetic says this SHOULD pass — that is the real finding
`_index_cache_view` (pooled_indexer.py:308-317) needs, with block B:
  semantic = B*528 ; subpages = B/256 ; tail = (B/256)*8448 = B*33
  required minimum stride = B*528 + B*33 = **B*561** — identical to `attn_page_size`.
And 256*561 = 143,616 = 8448 x 17, so **every** block that is a multiple of 256 yields a page
that is an exact multiple of `_INDEX_PAGE_BYTES`. At B=3328: 3328*561 = 1,867,008 = 8448 x 221,
remainder 0. Both the `>=` check (:311-313) and the `% 8448` check (:314) are satisfied by the
nominal page.

**Conclusion: `main_cache.stride(0)` is NOT `block_size * 561`.** Extra padding is being added
to the per-page stride somewhere downstream of interface.py (KV-cache tensor carve-up). Because
8448 = 256 x 33 and 33 is odd, ANY power-of-two rounding of the page breaks divisibility — which
is consistent with the symptom. I could not locate that site read-only within budget and I will
NOT guess at a patch anchor for it.

### The one number that closes this — temporary probe, not a shipped patch
Insert immediately before pooled_indexer.py:314 and run once:

    import os; print("[C4] shape", tuple(main_cache.shape), "stride", tuple(main_cache.stride()),
                     "need", block_size*561, "IPB", _INDEX_PAGE_BYTES,
                     "rem", int(main_cache.stride(0)) % _INDEX_PAGE_BYTES, flush=True)

`stride(0) - block_size*561` is the padding to explain; its factorisation tells us whether it is
a power-of-two alignment or a mamba-driven remainder.

## 4. TP=2 / 3 / 4 — computed with the fork's own calculators (num_spec=7, conv_kernel=4, dtypes bf16+fp32)
| TP | heads | local | conv state | ssm state | mamba_page | block | attn_page | attn_page % 8448 |
|---|---|---|---|---|---|---|---|---|
| 2 | 64 | 32 | (10, 12288) | (32,128,128) | 2,342,912 | 4352 | 2,441,472 | **0** |
| 3 | 66 | 22 | (10, 8448)  | (22,128,128) | 1,610,752 | 3072 | 1,723,392 | **0** |
| 4 | 64 | 16 | (10, 6144)  | (16,128,128) | 1,171,456 | 2304 | 1,292,544 | **0** |

Remainder is 0 for all three: **by this formula the constraint is not TP-specific, so TP=2 (the
lab's configuration) is equally exposed.** "The lab never hits this" is therefore NOT explained
by TP=3 head padding — which is further evidence the fault lies in the extra stride padding, not
in our 66-head/22-local shape.

Caveat on my own model: it predicts block 3072 / pad 6.99% for TP=3, the log says **3328 / 5.79%**
-> my mamba_page is ~9.6% low (back-solving the log gives ~1,764,825 B). The missing term is most
likely `MambaStateShapeCalculator.append_kda_recoverssm_record` (mamba_utils.py:308), a DFlash-only
extra state record. That record is the prime suspect for the odd stride. # NOT MEASURED

## 5. Minimal fixes, in order — NONE APPLIED
1. **`--mamba-cache-mode none` (env only, cheapest).** Default is `none` (cache.py:188); `align`
   is our deviation. `none` skips `mamba_block_size = block_size` (interface.py:981-982). Still
   compatible with `--enable-prefix-caching`, which requires `none` or `align` (cache.py:203).
   One env line, one restart. **Try this first.**
2. **Different `--block-size` (env only) — predicted NO effect.** Legal set is the intersection of
   B12X `MultipleOf(64)` and the pooled indexer's `% 256 == 0` => **multiples of 256**. Since every
   multiple of 256 already gives remainder 0, changing 256 -> 512 or 768 cannot fix a fault that
   lives outside `B*561`. Worth one cheap try only to falsify the model.
3. **`--mamba-cache-mode all`** takes the chunk-size LCM branch (interface.py:958-968) and yields a
   different block; second-cheapest env experiment.
4. **Patch, last resort:** round the allocated MLA page up to a multiple of `_INDEX_PAGE_BYTES`
   at the site the probe in section 3 identifies, marker `HAREM-TP3-LIL-C4`, count==1 anchor.
   Cannot be written before the probe — writing it blind would be guessing at the anchor.

---

# ROUND 2 (3 Sep 2026, after MAMBA_CACHE_MODE=none) — NOT SOLVED, NO PATCH WRITTEN

## A. Why `MAMBA_CACHE_MODE=none` changed nothing — my round-1 advice was WRONG
The flag DID reach vLLM: `DRY_RUN=1` renders `--mamba-cache-mode none` (argv 165-166), and
`start-lil.sh:301` is `--mamba-cache-mode "${MAMBA_CACHE_MODE:-align}"`, so the env is live.
It cannot help: in `vllm/platforms/interface.py` the mode only gates ONE line, `:981-982`
(`if mamba_cache_mode == "align": mamba_block_size = block_size`). The two lines that produce
the fault — `:970-974` (attn_block_size, the "3328 tokens" log) and `:984-1004` (the 5.79% mamba
pad) — run for `align` AND `none` alike. Withdrawing the round-1 recommendation.

## B. Proof that the MLA spec's own page is NOT the problem
- `kernel_block_alignment_size = max(min(64), block_size=256)` then `max(...,128)` for MLA
  = **256** (`interface.py:939-951`), so the chosen block is always a multiple of 256.
- `256 x 528 = 135,168 = 16 x 8448` and `256 x 33 = 8448`. Therefore for ANY block that is a
  multiple of 256, both the record part (`block x 528`) and the tail (`block x 33`) are exact
  multiples of `_INDEX_PAGE_BYTES`. At 3328: `1,757,184 = 208 x 8448`, `109,824 = 13 x 8448`,
  page `1,867,008 = 221 x 8448`.
- `MLAAttentionSpec.page_size_bytes` = `super().page_size_bytes + block*tail` (`kv_cache_interface.py:578-581`),
  `super()` returns `page_size_padded` when set (`:421-425`); `_apply_alignment_padding` (`:548-554`)
  is a no-op here because `alignment` is a DeepSeek-V4-only field, `None` for GLM (`:562`).
- `get_uniform_page_size` ASSERTS all pages in a group are equal (`kv_cache_utils.py:1023-1025`),
  and `unify_kv_cache_spec_page_size` never pads an MLA spec — MLA is explicitly excluded, it is
  either block-scaled (`:1108-1112`) or raises NotImplementedError (`:1117-1123`).
=> At spec level the group page is 1,867,008 and `% 8448 == 0`. Even an integer multiple of it
   (BLHNC interleaves layers, `qsa_cache.py:103`: "stride(0) spans every interleaved layer")
   stays a multiple. **So `stride(0)` must be carrying a FOREIGN pad from outside this chain.**

## C. Sites examined and CLEARED (file:line)
| site | verdict |
|---|---|
| `interface.py:939-1004` block/page/mamba padding | page = block x 561, always % 8448 == 0 |
| `kv_cache_interface.py:421-425, 548-581` MLA page composition + alignment | no-op for GLM |
| `kv_cache_utils.py:1062-1123` unify_kv_cache_spec_page_size | never pads MLA |
| `kv_cache_utils.py:1019-1025` get_uniform_page_size | asserts equality, cannot introduce a pad |
| `kv_cache_utils.py:1483-1513` promoted_page_size_padded | scales tail-free page, stays block x 561 |
| `kv_cache_utils.py:1880-1892` DFlash target/draft split | draft is an INDEPENDENT group |
| `kv_cache_utils.py:1970-1988` hidden-state align | GLM has no HiddenStateCacheSpec layers |
| `v1/worker/gpu/attn_utils.py:226-251` init_kv_cache -> allocate_kv_cache | delegates; not yet traced |

**NOT cleared, and the only remaining candidate: `allocate_kv_cache` and the BLHNC per-layer view.**

## D. Why I did not measure it, and why I did not patch
Three CPU-only reproduction routes were tried and all failed:
1. `EngineArgs.create_engine_config()` -> `DeviceConfig` gets an empty `device_type` with no driver.
2. Forcing `CudaPlatform` -> `vllm/platforms/cuda.py:23` imports `vllm._C_stable_libtorch`, which
   needs `libcuda.so.1`.
3. A generated `libcuda.so.1` stub (31 undefined `cu*` symbols, correct SONAME, with and without
   LD_PRELOAD) -> glibc `dl-tls.c:613 _dl_allocate_tls_init: Assertion listp != NULL failed`.

The one measurement that closes this is `main_cache.stride(0)` at `pooled_indexer.py:314`, which
only exists at runtime. Building `t3d` now would mean choosing a patch site by elimination rather
than by evidence. The two candidate patches are NOT equivalent and a wrong one is dangerous:
- Rounding the page up is only correct at the site that actually owns the stride; applied at the
  wrong site it changes memory accounting without changing the stride (fault persists), or changes
  both and silently desynchronises `parent_stride_pages` from the addressing the b12x indexer op
  uses -> wrong C4 pages read, i.e. silently wrong attention, not a crash.
- The coordinator's alternative ("compute `parent_stride_pages` from an exact-multiple sub-stride")
  is NOT possible in general: `_index_cache_view` addresses virtual page `p*parent_stride_pages + s`
  through a single `torch.as_strided` with stride `_INDEX_PAGE_BYTES` (`:334-339`). If
  `parent_stride_bytes % 8448 != 0`, consecutive parent pages are not at integer multiples of
  8448 and NO single stride can address them; it would need a different addressing scheme that the
  b12x kernel also consumes.

## E. What unblocks it in one step
Add to `pooled_indexer.py` immediately before `:314`, in a throwaway image, and start ONE rank:

    print("[C4]", tuple(main_cache.shape), tuple(main_cache.stride()),
          "need", block_size*561, "rem", int(main_cache.stride(0)) % _INDEX_PAGE_BYTES, flush=True)

`stride(0) - block_size*561` and its factorisation name the owner immediately. That requires
launching one rank, which the current instruction forbids — hence this is a question, not a step
I took.

---

# ROUND 3 — SOLVED (3 Sep 2026). Measurement closed it in one step.

Probe on rank 0 (print before `pooled_indexer.py:314`):

    shape=(1505, 3328, 528)  stride=(25559040, 528, 1)
    semantic=1757184  tail=109824  need=1867008  index_page_bytes=8448
    parent_stride_bytes=25559040   rem=3840   extra=23692032

## The chain, end to end (file:line)
1. `kv_cache_utils.py:1297-1303` — `bytes_per_block = max over groups of
   sum(page_size_bytes of that group's layers)`. The **DFlash draft group wins**:
   5 layers x 3328 tokens x 1536 B/token = **25,559,040**
   (drafter bf16 KV at TP=3 = 3 local KV heads x 2 x 128 x 2 B).
2. `kv_cache_utils.py:1387-1388` — `interleaved_block_stride = bytes_per_block`
   (BLHNC is block-outermost).
3. `kv_cache_utils.py:1420-1424` — fed to `compute_layout_strides` as the fixed
   block stride -> `KVCacheTensor.block_stride`.
4. `kv_cache_interface.py:326, 352-358` — `create_kv_cache_views` builds the MLA
   view with that stride -> the measured `stride(0)`.
5. `pooled_indexer.py:314-317` — rejects it: `25,559,040 % 8448 = 3840`.

Arithmetic: `8448 = 2^8 * 3 * 11`; `3328 = 2^8 * 13`; `7680 = 2^9 * 15`. Neither
factor supplies the 11, so the product misses it. **Round 2 was right that the MLA
page is innocent** (`3328*528 = 208*8448`, `3328*33 = 13*8448`, page
`1,867,008 = 221*8448`) — the fault was one level up, in the pool stride, and the
draft group is what put it there. At TP=2/4 the drafter's per-token bytes and the
chosen block differ, which is why the lab's TP=2 never hit it.

## The fix already existed in this fork, gated behind the wrong flag
`kv_cache_utils.py:1310-1319` already rounds `bytes_per_block` up to `64*132`,
with the fork's own comment: *"The block-outermost pool stride must preserve the
index-page unit even when a larger recurrent-state group determines pool
capacity."* That is verbatim our failure. But the condition is

    os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None
    and contains_glm5_next_mla

`HAREM-TP3-LIL-C4` drops the env-var half and keeps `contains_glm5_next_mla`
(`_contains_glm5_next_mla`, `:1285-1291`, matches `MLAAttentionSpec` with
`model_version == "glm5_next"`). One count==1 anchor, one file.

    bytes_per_block  25,559,040 -> 25,563,648   (+4,608 B/block, +0.018%)
    25,563,648 = 8448 * 3026, remainder 0; subpages_per_parent = 13

## Correctness
- `round_up` only grows the stride; `pooled_indexer.py:311-313` (`stride >=
  semantic + tail`) still holds, and `:314` now passes by construction.
- `bytes_per_block` is the single source for BOTH `num_blocks =
  available_memory // bytes_per_block` (`:1390`) and the block stride (`:1388`),
  so capacity accounting and addressing move together — no drift.
- Uniformity: every group's tensor takes the same `interleaved_block_stride`
  (`:1388`, `:1420-1424`), so `allocate_kv_cache`'s "KV cache tensors must share
  one backing allocation" assert (`worker/utils.py:393`) still holds, and
  `size = bytes_per_block * num_blocks` (`:1392`) grows consistently.
- The extra 4,608 B sit at the end of each block, after every group's pages — the
  "pad" in the fork's own block-outer diagram (`:1396-1400`). Nothing addresses them.
- `parent_stride_pages = 3026` is now exact, so `_index_cache_view`'s virtual page
  id `p*3026 + s` stays a bijection consistent with the b12x indexer op. This is
  precisely the invariant the fork's comment says the round-up protects.
- No new failure mode at `kv_cache_interface.py:333-346` (the `ratio > 1`
  dense-page check): the measured MLA view has `shape[0] == num_blocks == 1505`,
  so `ratio == 1` and that branch is not taken. Any group with `ratio > 1` would
  already be failing today, since the stride already differs from the dense page.

## Rejected
- Setting `VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE` — it also flips
  `kv_cache_utils.py:1931-1932` into the split-cache grouping path, a much larger
  behaviour change than the round-up.
- `--block-size 5632` — pads the KDA/mamba page by ~79%.

## Cost
+4,608 B per block out of 25,559,040 = **+0.018%**. At 1505 blocks that is 6.9 MB
total. KV capacity effect is below the reporting resolution of the KV-size log line.
