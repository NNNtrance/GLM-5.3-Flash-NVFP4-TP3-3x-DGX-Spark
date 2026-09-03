# 08 — What we tried and rejected

This is the catalogue of branches we opened and closed on the way to the production setup. It exists
because a recipe that only lists what works hides most of the cost of building it. Several of the
entries below are the reason a flag in [03 — Launch command and every flag](03-launch-and-flags.md)
has the value it has.

This document does **not** repeat the accepted settings (see
[03 — Launch and flags](03-launch-and-flags.md)), the quality numbers
([06 — Benchmarks](06-benchmarks.md)), the speed tables ([07 — Speed](07-speed.md)) or the memory
ladder ([05 — Memory ladder](05-memory-ladder.md)). Unsolved items and withdrawn claims live in
[09 — Open problems](09-open-problems.md).

## How to read the numbers here

Unless a row says otherwise, everything below was measured with:

```text
image harem/glm53-lil:t10 (vLLM LIL fork, full source build) · TP=3 + expert parallel
MoE backend marlin · NVFP4 weights · KV dtype fp8 (fp8_ds_mla) · speculative DFlash2 k=7
CUDA graphs on + AOT compile · KDA cache mode align · --block-size 256
--max-num-batched-tokens 2048 · --max-num-seqs 8 · gpu-memory-utilization 0.85
no --kv-cache-memory pin · thinking on, reasoning effort low · temperature 0
speed prompts: 12 short English code prompts (realistic set), two rounds, C1..C8 = concurrency
```

Production later moved to `gpu-memory-utilization 0.88`; the speed and quality numbers in this
document were taken at 0.85 unless the row says 0.88. Branch names in parentheses (`t4b`, `t12c`,
`t17`, …) are our internal image tags; they appear here only so that the log lines and the patch
files in [`patches/`](../patches/) can be matched to the experiment that produced them.

Evidence tiers are the ones from the style guide, with one addition: `[measured-here, raw not
published]` means we measured it on this cluster but the raw file is not in [`results/`](../results/)
— it exists on our nodes only. That is different from `[measured-here, raw lost]`, where the file no
longer exists anywhere.

## Catalogue

| # | Branch | Verdict | Tier | Date |
|---|---|---|---|---|
| 1 | EXL3 path (4 bpw checkpoint + our own image; plus the fully quantized 4.05 bpw package) | Parked as backup | `[measured-here, raw not published]` | 2026-09-02 |
| 2 | A third-party vLLM EXL3 plugin with its own kernels | Not tried | `[reported]` | 2026-09-03 |
| 3 | Intel int4 AutoRound (W4A16) | Out of scope, deleted | `[measured-here, raw not published]` | 2026-09-01 |
| 4 | Other NVFP4 checkpoints (three publishers) | Eliminated / not run | `[measured-here]` + `[reported]` | 2026-08-29 … 2026-09-02 |
| 5 | Official FP8 weights | Does not fit | `[estimate]` | 2026-08-29 |
| 6 | Uncensored / abliterated checkpoints | Deferred, with a return condition | `[reported]` | 2026-09-02 |
| 7 | b12x MoE backend with expert parallel (`t4`, `t4b`) | Closed — corrupt output | `[measured-here, raw not published]` | 2026-09-03 |
| 8 | MXFP8 draft model (`t12`, `t12b`, `t12c`) | Parked — loader does not cover it | `[measured-here, raw not published]` | 2026-09-03 |
| 9 | fp8 draft KV (`t14`, `t14b`, `t14c`) | Parked — engine will not start | `[measured-here, raw not published]` | 2026-09-03 |
| 10 | Split KV page layout (`t15`) | Rejected — 53.7 GiB at 1M | `[measured-here, raw not published]` | 2026-09-03 |
| 11 | Stock vLLM image + this checkpoint (`t11`) | Parked — no DFlash2 support | `[measured-here, raw not published]` | 2026-09-03 |
| 12 | Speculation off (`t9`) | Diagnostic; kept as a strict-determinism fallback | `[measured-here, raw not published]` | 2026-09-03 |
| 13 | DFlash2 k > 7 (k=8, k=10) | Rejected — k=7 fixed | `[measured-here, raw lost]` | 2026-08-31 |
| 14 | Engine-native MTP-4 instead of DFlash2 | Rejected — slower | `[measured-here, raw not published]` | 2026-08-31 |
| 15 | Newer DFlash2 draft revision (`t13b`) | Reverted to the older revision | `[measured-here, raw not published]` | 2026-09-03 |
| 16 | Draft head padding 32→36 as a KV lever | Rejected — pool −58.6 % | `[measured-here, raw lost]` | 2026-08-31 |
| 17 | Turning thinking off (`enable_thinking=false`) | Forbidden — no such switch | `[measured-here]` | 2026-08-30 … 2026-09-03 |
| 18 | `reasoning_effort` values other than low/high | Rejected — silently becomes max | `[measured-here]` | 2026-09-03 |
| 19 | CUDA graphs off (`--enforce-eager`, `t16`) | Kept as a documented option | `[measured-here, raw not published]` | 2026-09-03 |
| 20 | Limited graph capture sizes (`t17`) | Middle ground, not selected | `[measured-here, raw not published]` | 2026-09-03 |
| 21 | `cudagraph_mode=PIECEWISE` | Will not be opened | `[reported]` | 2026-09-02 |
| 22 | KV pin (`--kv-cache-memory`) instead of profiling | Rejected in favour of profiling | `[measured-here, raw lost]` | 2026-08-31 |
| 23 | `gpu-memory-utilization` 0.89 and 0.90 | Rejected — swap / precheck failure | `[measured-here]` | 2026-09-03 |
| 24 | `--block-size` 128 / 2304 / 5632 | Rejected — 256 wins | `[measured-here, raw lost]` | 2026-08-31 |
| 25 | `--max-num-batched-tokens 8192` | Rejected — pool −28 % | `[measured-here, raw lost]` | 2026-08-30 |
| 26 | Temperature as a speed lever | Rejected — no effect | `[measured-here, raw not published]` | 2026-09-03 |
| 27 | Prefix caching off (`t8`) | Rejected — neutral, left on | `[measured-here, raw not published]` | 2026-09-03 |
| 28 | Alternative weight loaders | Rejected — slower or uninstallable | `[measured-here, raw lost]` | 2026-08-31 |
| 29 | Disabling FlashInfer autotune warm-up | Rejected — moves the cost into service | `[reported]` | 2026-08-31 |
| 30 | CPU core pinning (`--cpuset-cpus`) and DS+HND placement | Kept, but the reason is weak | `[measured-here, raw lost]` | 2026-08-31 |
| 31 | `vm.swappiness=0` | Reverted — locked all three nodes | `[measured-here, raw lost]` | 2026-09-02 |
| 32 | TP=2 on two nodes instead of TP=3 | Rejected before purchase-level commitment | `[reported]` | 2026-08-29 |
| 33 | PyTorch 2.14 upgrade | Not now — watch list | `[reported]` | 2026-09-03 |
| 34 | Harness defaults (needle timeout, lm-eval queue timeout) | Both had to be changed | `[measured-here]` | 2026-09-03 |

---

## Checkpoint and format branches

### 1. The EXL3 path

**What.** `brandonmusic/GLM-5.3-Flash-tr3-4bpw`, a 4 bpw EXL3 checkpoint (163.6 GiB, routed experts
quantized, attention and shared expert left unquantized), served by our own image built on the same
base image as the production one, with the `MiaAI-Lab` EXL3 overlay (commit `c707598`), the
`FlyCockpit` three-node TP=3 patches, exllamav3 pinned at `c5d9c65`, plus two patches of our own (do
not narrow EXL3 tensors under expert parallel; pin the expert map). Full attribution in
[CREDITS](../CREDITS.md).

**Why we tried it.** Published KL-divergence panels put EXL3 at 4 bpw around 0.025–0.035 nats
against roughly 0.045–0.061 for NVFP4 checkpoints of the same model `[reported]`, and the checkpoint
is 22 GB smaller per node than the NVFP4 one, which is worth roughly 20 % more KV pool.

**A structural constraint worth knowing.** EXL3 tensors cannot be split for TP=3. Storage is in tiles
of 16, but correctness is tied to the Hadamard block of 128; a slice that is not a multiple of 128
produces more than 10 % error, and `hidden 4096 / 3` and `moe_intermediate 2048 / 3` are not even
integers. The only way out is expert parallelism — the expert stays whole (288 / 3 = 96) and no
tensor is cut — which works only as long as everything outside the experts is left unquantized. A
fully quantized EXL3 package (attention included) therefore cannot run TP=3 at all; it is TP=2 or
pipeline parallel. `[reported]`, source reading, 2026-09-02.

**The other EXL3 package we evaluated and did not run: `turboderp/…-exl3` at 4.05 bpw.** On paper it
is the better checkpoint (KL 0.0345 mean, 0.0031 median). We downloaded it, read its `config.json`
(branch `4.05bpw`, commit `2a30229e`) and stopped there for two reasons. First, it will not load in
the community plugins as it is: its codebook is `mul1` where the plugins accept only `mcg`, its
tensors carry a `.mul1` suffix under a `model.language_model.layers.N` prefix, its `bits: 4.05` is a
float that `int()` silently turns into 4, and — the real work — **everything** is quantized
(attention qkv/o, dense MLP, head), while the plugins are scoped to routed experts only and have no
dense EXL3 linear at all. Second, and decisively, because everything is quantized, the constraint
above applies to attention too: **TP=3 is impossible for this package**. That is days of plugin work
to end up on two nodes. `[not tested]`, 2026-09-02.

**Result.**

| Measure | EXL3 candidate | Production (t10) |
|---|---|---|
| Code exam / correctness probe | 12/12 · 10/10 | 12/12 ×3 · 10/10 |
| MMLU sample (57 × 35 ≈ 2,000 q, 0-shot) | 87.0 | 86.5–86.7 |
| C8 total, realistic prompts | ≈ 106 tok/s | 146 tok/s |
| Prefill, 7k prompt, uncached | 500–740 tok/s | 1,585 tok/s |
| TTFT for a 7k prompt under load | 22–24 s | 4.8 s |
| Boot to serving | 11–13 min | ≈ 5 min |
| KV pool at 0.85 | 4.32 M tokens | 3.86 M tokens |

The A/B rounds on this branch accepted two settings (disabling the decode-floor prefill deferral cut
C2 TTFT from 5.9 s to 0.65 s and raised C2 throughput by 33 %; `max_num_seqs 8` raised C8 by 20 %)
and rejected two (`--max-num-batched-tokens 2048` cost 6.5 % of the pool for no decode gain; CUDA
graphs captured cleanly but gave no speed inside the run-to-run band and cost 4.2 % of the pool).
One was left suspended: a "fat" MoE kernel flag worth about 10 % on prefill.

**Why parked.** Quality was equal within noise (±0.75 points on the MMLU sample is our repeatability
band), but throughput at concurrency was ~30 % lower and prefill was 2–3× slower, which matters for
an agent fleet with long prompts. Parked as the backup path on 2026-09-02; the candidate env file is
kept on our nodes and is not published here. The checkpoint was deleted from the three nodes on
2026-09-03 to free 164 GB.

**What it cost to park it.** We gave up the smaller checkpoint (≈ 20 % more KV pool) and a possibly
slightly better quantization error, in exchange for prefill, TTFT and concurrency.

### 2. A third-party vLLM EXL3 plugin

**What.** A vLLM plugin that ships its own CUDA kernels for EXL3 (dense and MoE GEMM, sparse-MLA
decode) with CUDA graph and tensor-parallel support. `[reported]`, source and README reading only,
2026-09-03.

**Why we looked.** Its author measured its EXL3 GEMM at 1.2× (large batch) to 4.7× (small batch)
faster than the kernel our EXL3 branch used, with outputs agreeing to 6e-4 — meaning part of what we
measured as "EXL3 is slow" was the kernel, not the format.

**Why not tried.** It is developed and measured on 4× RTX PRO 6000 (sm_120). Multi-node is explicitly
not implemented, TP=3 does not exist in it, and its MLA kernel assumes a full 16 heads (TP=4), so our
22-head shape would need tile padding — the same class of problem we hit in the production kernel
(see [02 — Image build](02-image-build.md)). Compatibility with DFlash2 speculation is untested by its
author. `[not tested]` by us.

**If someone wants to revisit it**, the zero-risk first step is to build it for sm_121 on one node and
run its model-free kernel benchmarks. That produces real GB10 numbers in an hour or two and touches
nothing in production.

### 3. Intel int4 AutoRound (W4A16)

**What.** `Intel/GLM-5.3-Flash-W4A16-AutoRound`, 169.0 GiB, tried in three configurations: TP=3 with the MoE
intermediate padded to 2112 on the MoeWNA16 backend, TP=3 with 2304 on marlin, and TP=2 with no
padding on marlin.

**Result.** The TP=3 / 2112 arm produced the largest KV pool we ever saw on this model (5,541,619
tokens, unpinned) but locked into repetition ("17 × 23 + 17 × 23 …") and returned content on only
6 of 9 probe questions. The TP=3 / 2304 marlin arm was worse — it parroted the question back. The
TP=2 arm with no padding scored 10/10 on the correctness probe, 12/12 on the code exam and produced
zero empty responses. That triangulation is the useful part: **the defect was not in the checkpoint,
it was in our TP=3 loader path**, where we measured a 5.9 % band shift against the reference (the
same measurement on the NVFP4 checkpoint gave 1.5 %). The marlin gate is closed for this shape
anyway: `704 % 128 = 64 ≠ 0`.

**Why rejected.** Declared out of scope on 2026-09-01. It was downloaded again on 2026-09-02 as a
comparison candidate, but **its benchmark battery was never run** — an honest gap, since the only
int4 numbers we have for it are the broken TP=3 arms. Deleted on 2026-09-03 (170 GB).

### 4. Other NVFP4 checkpoints

Three other NVFP4 publishers were evaluated before we settled on the one in
[00 — Prerequisites](00-prerequisites.md).

| Checkpoint | Size | What we found | Verdict |
|---|---|---|---|
| `orcarouter` (custom compressed-tensors export; this was our production model for a while) | 177.2 GiB | gate/up scale defect on 68.5 % of experts, no input scales (W4A4 falls back to A16 under marlin), no MTP head, KL 0.073 against the FP8 parent (not against BF16). The repository later went gated. | Eliminated |
| `LibertAIDAI` and `dealignai` (ModelOpt exports) | 181.3 GiB | `U+FFFD` corruption, 86 occurrences across 6 runs and 94 across 6 — the same class as upstream vLLM issue **#54150**; gate ≠ up in 69.1 % of expert pairs | Eliminated |
| `RedHatAI` (llm-compressor, W4A4) | 184.3 GiB | `U+FFFD` 0 occurrences across 6 runs — llm-compressor gives gate and up a **single shared global scale by construction**, so the `[:, 0]` shortcut is exact for it | Never run; still a valid candidate |

**The defect itself is worth stating once, because it is checkpoint-independent.** Every MoE expert
in this model quantizes `gate_proj` and `up_proj` with separate global scales. vLLM fuses them into
one `w13` tensor and takes the **gate's** scale unconditionally (`w13_weight_scale_2[:, 0]`), so the
up half is dequantized with the wrong scale. There is no compensation, and the fusion happens before
the backend, so marlin, cutlass and b12x all inherit it. The engine prints
`w1_weight_global_scale must match w3_weight_global_scale. Accuracy may be affected.` and continues.
We measured it directly from the safetensors headers on two nodes independently: 68.5 % of 12,096
expert pairs mismatched, median |r − 1| of 7.2 %, p99 of 49 %, maximum 260 %, mean ratio 1.054, and
the mismatch rate rises with depth (81 % in the last layers). This is the same root cause that
`murai-labs` published for upstream #54150, arrived at independently here from the checkpoint headers.
`[measured-here, raw not published]`, 2026-09-02.

We wrote a three-line fix that folds the ratio into the per-expert output scale, verified it on CPU
(error 4.4e-2 → 3.6e-7 with no clamping), and then found that the model's `swiglu_limit=10` clamp
makes the correction over-correct where clamping is active: at a 9.6 % clamp rate it still helps
(3.6e-2 → 2.4e-2), at 40 % it hurts (2.4e-2 → 3.7e-2). The real clamp rate on real traffic was never
measured, and the whole line of work stopped when we moved to a checkpoint that quantizes gate and
up correctly. **The production checkpoint (`local-inference-lab/GLM-5.3-Flash-NVFP4`) does not have
this defect**, which is the main reason it was chosen.

### 5. Official FP8 weights

305.8 GiB, i.e. 101.9 GiB per node before KV, activations and the driver's own ~14 GiB. Does not fit
on three GB10 nodes. `[estimate]` from the published file sizes, 2026-08-29. Eliminated without a run.

### 6. Uncensored / abliterated checkpoints

Five abliterated repositories were surveyed. **None of them publishes a BF16 comparison** — the
quality cost of the abliteration is unmeasured in every case, and at least one publisher of an
abliterated build of another model states plainly that its KL panel was measured *without* the
abliteration applied. The censored candidates we did evaluate have far better evidence behind them.

**Verdict: deferred, quality first.** The return condition is written down so it can be checked
rather than argued: an uncensored repository whose loss against BF16 has been published and sits at
noise level. Refusal softening, if we need it, is a harness-side system-prompt matter and not a
weights matter. `[reported]`, 2026-09-02.

---

## Engine and kernel branches

### 7. b12x MoE backend with expert parallel (`t4`, `t4b`)

**What.** The b12x MoE backend rejected expert parallelism (it only understood a W4A16 expert map),
so we patched `fused_moe` and the expert routing map to accept it. The CPU unit test passed 6/6 and
the image built.

**Why we tried it.** marlin is weight-only, so it drops the checkpoint's W4A4 activation scales and
runs A16. The b12x path would have kept them — the only route we had to recover that quality.

**Result.** The engine started and served, and the output was wrong: correctness probe 7/10, code
exam **0/12**. KV pool 4.18 M, prefill 1,221 tok/s, cold C1 TTFT 30.8 s.

**Why closed.** Corrupt output with a passing CPU unit test means the unit test tested the wrong
thing. Closed on 2026-09-03. **Reopening condition: a numerical unit test against a reference
implementation, on GPU, before any end-to-end run.** The cost of leaving it closed is that the
marlin W4A4→A16 fallback quality cost stays unmeasured — see [09 — Open problems](09-open-problems.md).

### 8. MXFP8 draft model (`t12`, `t12b`, `t12c`)

**What.** `local-inference-lab/GLM-5.3-Flash-DFlash2-MXFP8`, an MXFP8 conversion of the DFlash2 draft
(converted from the same upstream revision we run, lm_head excluded, 1×32 blocks, E8M0 scales).
Expected gain: about 1 GB of weights per node and a slightly faster draft pass.

**Result.** Three attempts, no successful start.

- `t12` with the stock 32/8 head config: `assert total_num_heads % tp_size` — the draft must be
  padded to 36/9 for TP=3, exactly as the BF16 draft is.
- `t12b`: a race artefact of our own deployment, not a real result.
- `t12c` with 36/9: rank 2 died with `start (768) + length (384) exceeds dimension size (1024)`.
  Root cause: our pad-then-narrow TP=3 loader only covers the BF16 weight paths. The MXFP8
  `k_proj`/`v_proj` (8 KV heads × 128 = 1024 rows) go through the ModelOpt weight-plus-scale path,
  which the patch never touches.

**Why parked.** The fix is understood (extend the pad-then-narrow loader to the quantized path) but
the prize is about 1 GB of weights, so it did not earn the patch. Parked 2026-09-03; the local copy
was deleted the same day. See [09 — Open problems](09-open-problems.md).

### 9. fp8 draft KV (`t14`, `t14b`, `t14c`)

**What.** Storing the draft model's KV in fp8. The arithmetic was attractive: the block stride drops
from 25.56 MB to 20.54 MB, which would have been about **+20 % KV pool**.

**Result.** Three variants, none started.

- `t14`: `kv_cache_interface.py:339 … block stride 20,537,088 != page 2,555,904`. Once the draft
  group is no longer the largest group, its page stays padded and the kernel block no longer divides
  the manager block.
- `t14b` with `VLLM_KV_CACHE_LAYOUT=LBNHC`: this model only permits `BLHNC`.
- `t14c` with the draft attention moved to the B12X backend: `B12X … block_size not supported`.

**Why parked.** The real fix is to make the kernel block equal the manager block, which is a patch we
did not write. Parked 2026-09-03.

### 10. Split KV page layout (`t15`)

**What.** `VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE=256` with the mamba page at 3328, to stop the draft
group's page layout from inflating the whole pool.

**Result.** The engine asked for **53.73 GiB of KV for a single 1M-token request** against 31.85 GiB
available. Cause: the KDA states are stored per block, which pushes the per-token cost to about
56 KB against our 8.6 KB.

**Why rejected.** Rejected on the arithmetic, 2026-09-03; it is worse than the problem it solves at
the context length we care about.

### 11. Stock vLLM image with this checkpoint (`t11`)

**What.** The untouched public base image plus our NVFP4-era launcher, as a "does the model itself
behave" control.

**Result.** Did not start: the stock engine does not know the `DFlash2DraftModel` architecture. Our
earlier NVFP4-era image did carry DFlash2 support, but that image had been deleted.

**Why parked.** The control we actually needed was provided by the speculation-off run below, which
answers the same question on a stack we can still build. Parked 2026-09-03; the base image is now
kept on all three nodes (30.7 GB) so this can be repeated without a download.

### 12. Speculation off (`t9`) — kept as a fallback

**What.** The production stack with speculative decoding disabled. Run as a diagnostic while the
output was still unstable.

**Result.** Correctness probe 10/10, code exam **12/12 four times over** (chain plus three repeats),
and the two rounds were bit-identical — fully deterministic. Speed collapses, and the KV pool grows
by more than half because the draft group disappears:

| | C1 | C2 | C4 | C6 | C8 per user | C8 total | KV pool |
|---|---|---|---|---|---|---|---|
| Speculation off (`t9`) | 20.4 | 16.1 | 13.0 | 9.8 | 9.7 | 74 | 5,934,911 |
| Production (`t10`) | 56.9 / 56.5 | 42.7 / 39.5 | 29.5 / 28.8 | 22.9 / 22.8 | 21.8 | 145.8 | 3,860,869 |

Realistic code prompts, effort low, temperature 0, 0.85, two rounds. `[measured-here, raw not
published]`, 2026-09-03.

**Why this branch mattered.** It proved the model and the checkpoint were clean and pointed the
investigation at the fork's speculative path, where the real bug was (see
[02 — Image build](02-image-build.md) for the 22→24 head story).

**Why the draft still wins for code.** DFlash2 buys 2.8× on single-stream code and roughly 2× on
total throughput, at a cost of about 35 % of the KV pool. For prose the draft barely holds
(acceptance ≈ 13 %) and speed falls back to roughly the speculation-off level, so the draft is close
to free rather than a win there. If you need bit-level determinism at temperature 0, this is the arm
to run — see the near-tie item in [09 — Open problems](09-open-problems.md).

### 13. DFlash2 k > 7

**What.** k=8 and k=10 measured against k=7 on 2026-08-31.

| | C1 | C2 | C4 | C6 | C8 | acceptance length | KV pool |
|---|---|---|---|---|---|---|---|
| k=7 | 47.38 | 65.89 | 93.28 | 113.19 | 133.15 | 4.99–5.30 | 5,392,258 |
| k=8 | 47.34 | 71.13 | 92.86 | 114.60 | 134.52 | 5.16–5.51 | 5,332,675 (−1.1 %) |
| k=10 | 47.99 | 71.35 | 90.78 | 109.05 | 130.14 | 5.33–5.99 | 5,217,374 (−3.2 %) |

NVFP4 era, KV pinned at 34.21 GiB, thinking on at effort low. The classic speculative trade-off is
visible: acceptance length keeps rising with k while speed turns over, because the compute spent on
rejected drafts rises too.

**Why rejected.** Two reasons, and the second is the real one. First, the three arms were measured at
different machine uptimes and the k=7 control re-run never produced a scan file, so the comparison is
not clean — `[measured-here, raw lost]`. Second, in a separate clean test above k=7 the model produced
degraded output. **k=7 is fixed and a depth study will not be run.** This is our own report with no
depth study behind it; treat it as a decision, not as a measured optimum.

### 14. Engine-native MTP-4 instead of DFlash2

Measured against DFlash2 k=7 on the same stack: DFlash2 was 7–23 % faster across C1–C8, with
acceptance length 4.48–5.08 against 3.37–3.52 for MTP-4, even though the MTP-4 arm ran at higher
concurrency. `[measured-here, raw not published]`, 2026-08-31. DFlash2 kept.

### 15. A newer DFlash2 draft revision (`t13b`)

**What.** `incoai/GLM-5.3-Flash-DFlash2` was updated on 2026-08-31 (revision `bf582e4e`) — identical
config, different weights. We run the previous revision, `dc77ff1c`. Note the draft's licence:
cc-by-nc-nd-4.0 plus a project-specific, non-transferable permission we obtained for this project.
We do not redistribute the draft and our permission does not extend to you; see
[CREDITS](../CREDITS.md).

**Result.** Speed and acceptance were indistinguishable from production: C1 57.5 / 55.3, C8 total
143.6, acceptance 60–65 % against production's 56.9 / 56.5, 145.8 and 62–65 %. Quality: correctness
probe 10/10, code exam 12/12 then 11/12 then 11/12 — **the same task (a spiral matrix) failed
deterministically in both repeats**, while the older revision and the speculation-off arm both get
it right.

**Why reverted.** No measurable gain, and one reproducible regression we could not explain. We stayed
on `dc77ff1c`. **This is an honest open item, not a solved one**: with exact greedy verification a
draft model cannot change the output at all, so the fact that it did means our verification pass is
not exact. See the near-tie item in [09 — Open problems](09-open-problems.md). We did not root-cause
it, and we did not rule out that the newer revision is simply better and our older one is masking the
same instability.

### 16. Padding the draft heads 32→36 as a KV lever

Opening the draft config from 32 heads / 8 KV to 36/9 is required for TP=3 and is part of the
production setup. Trying to use it as a memory lever is not: measured on its own it took the pool
from 5,476,368 to 2,264,924 tokens (**−58.6 %**), with per-token cost rising from 7,100 to 17,166
bytes. Padding to 33/9 does not start at all (`num_heads (11) is not divisible by num_kv_heads (3)`).
`[measured-here, raw lost]`, 2026-08-31. Only the `draft_tensor_parallel_size=1` path is used.

---

## Serving-behaviour branches

### 17. Turning thinking off

**What.** Repeated attempts to serve this model without a thinking block, via
`chat_template_kwargs {"enable_thinking": false}`.

**Result.** **There is no such switch in this model's chat template.** The string `enable_thinking`
does not occur in it; only `reasoning_effort` and `clear_thinking` do, and the template always ends
the prompt with `<|assistant|><think>`. Passing `enable_thinking=false` does not stop the model
thinking — it only disables the extraction filter, so the reasoning leaks into the answer
("392392", "Paris.Paris"). An older checkpoint of the same model family *did* have the switch in its
template, which is why a measurement from 2026-08-30 appeared to show it working; that measurement
was template-specific and has been withdrawn (see [09 — Open problems](09-open-problems.md)).

**The mirror-image failure.** With thinking on and effort `low`, the model sometimes answers inside
the reasoning block without emitting `</think>`, so `content` arrives **empty** while `reasoning`
holds the answer. On the earlier stack this happened in about 11 % of short requests (4 of 36). The
fix costs nothing: one mandatory system line, *"Always write the final answer as your reply, outside
your reasoning."* — 0 empty responses in 100 requests, average output 6.3 → 6.5 tokens.

**On the production stack we could not reproduce the phenomenon at all**: 40 varied trivial questions
(English and Turkish) × effort low at temperature 0 gave **0/40** empty content and 0/40 tag leakage,
with or without the directive line; a 3 questions × 4 settings × 6 repeats matrix gave **0/72**; and
the correctness probe across roughly 15 arms of a full night gave zero. We keep the directive line
anyway as a zero-cost safety net, and any client should fall back to `reasoning` when `content` is
empty. `[measured-here]`, 2026-09-03.

Note for anyone chasing tag leakage: during the period when the decode kernel was miscomputing (see
[02 — Image build](02-image-build.md)), we did see `</think>` leaking into content. After the head
padding fix it never appeared again. The leakage was a symptom of broken generation, not of the
parser.

**What we use instead.** `reasoning_effort: low`. The speed cost of thinking being on is zero
(C1 28.94 against 28.91, C8 94.04 against 97.11 on the same engine).

### 18. `reasoning_effort` values other than low and high

The template whitelists exactly `low` and `high`. `medium`, `none`, and omitting the field entirely
all resolve **silently** to `max`. Measured token counts on the same question: low 13, high 38,
"medium" 45 — the middle value behaves like max, not like a middle. `[measured-here]`, 2026-09-03.
Production uses `low`; the effort tiers we use for different agent roles are in
[06 — Benchmarks](06-benchmarks.md).

### 19. CUDA graphs off (`--enforce-eager`, `t16`)

**What.** The same production image with graph capture disabled. This was the production setting for
the whole NVFP4 era and it is still a reasonable choice, so it is documented as an option rather than
as a rejected branch.

| | C1 | C2 | C4 | C6 | C8 per user | C8 total | KV pool |
|---|---|---|---|---|---|---|---|
| Full graphs (`t10`, production) | 56.9 / 56.5 | 42.7 / 39.5 | 29.5 / 28.8 | 22.9 / 22.8 | 21.8 | 145.8 | 3,860,869 |
| Eager (`t16`) | 47.1 / 45.8 | 39.4 / 41.2 | 30.5 / 30.9 | 24.1 / 24.5 | 22.7 / 21.8 | 155.0 / 145.6 | 4,365,217 |
| Graphs at sizes 8 and 16 only (`t17`) | 56.1 / 52.2 | 38.6 / 41.9 | 29.1 / 30.5 | 23.6 / 23.6 | 22.4 | 147.6 | 4,063,768 |

Realistic code prompts, two rounds, effort low, temperature 0, 0.85. Quality was 10/10 and 12/12 on
all three arms. `[measured-here, raw not published]`, 2026-09-03.

**Reading.** Graphs buy 22 % on single stream, lose about 5 % at C4–C6, are level at C8, and cost
**12 % of the KV pool**. We chose full graphs because single-stream latency is what a person feels;
if you are running 6–8 concurrent agents and want the pool, eager is defensible and the cold-start
penalty is real (cold C1 32.8 tok/s eager against 42.9 with graphs).

**What this costs:** 12 % of the KV pool, about 500 k tokens at 0.85, of which the memory ladder
later recovered most (see [05 — Memory ladder](05-memory-ladder.md)).

### 20. Limited graph capture sizes (`t17`)

`--cudagraph-capture-sizes 8 16` is the honest middle: C1 close to full graphs, C4 and above close to
eager, and the pool 5 % better than full graphs (7 % worse than eager). We recorded it and did not
select it, because it also inherits the code-exam wobble on the one sensitive task (11/12 on that
run) and we did not want a third arm to maintain. It is the first thing to try if the pool matters
more to you than it does to us.

### 21. `cudagraph_mode=PIECEWISE`

Eliminated from sources, not measured. Upstream vLLM issue **#53030**: breakable graphs plus
PIECEWISE plus speculative decoding causes every draft to be **silently rejected**, pinning
acceptance length to 1.00 with no error raised — throughput falls by roughly 4× and the run reads as
"graphs are a disaster". Upstream's own fix PR (#53061) turns PIECEWISE into NONE for this
configuration, so the vendor's answer is also "no graphs here". `[reported]`, 2026-09-02.

### 22. KV pin instead of profiling

**What.** Fixing the KV pool with `--kv-cache-memory` (our NVFP4-era production setting, 34.21 GiB)
against letting the memory profiler size it.

**Result.** The pin gave a larger pool — 5,392,258 tokens against 4,217,735 from the profiler, a
21.8 % difference. `[measured-here, raw lost]`, 2026-08-31.

**Why rejected anyway.** With the pin in place there is no activation headroom, and
`profile_cudagraph_memory()` is never called at all — the graph pool is taken from whatever RAM
happens to be free. The failure mode is not a clean OOM: it is a 17-minute
`No available shared memory broadcast block` lockup that the OOM killer never notices and that ends
in pulling the power. The production stack runs with no pin and sizes the pool with
`gpu-memory-utilization` (see [05 — Memory ladder](05-memory-ladder.md)).

**What this costs:** up to about 20 % of the pool on paper, in exchange for a safety margin and for
being able to test anything that needs memory.

### 23. `gpu-memory-utilization` 0.89 and 0.90

0.89 works and gives 4,408,695 tokens (+13.6 % over 0.85), with quality unchanged at 10/10 — but the
head node starts swapping **927 MB** against 439 MB at 0.88, and its "free memory" number becomes
misleading because the kernel produced it by paging engine memory out (container RSS 10.2 → 8.8 GiB).
Rejected on 2026-09-03; production is 0.88.

0.90 on the NVFP4-era stack failed differently and instructively: the precheck raised a `ValueError`
and the engine exited in 53 s **having allocated nothing** — a safe failure (requested 109.46 GiB
against 107.54 free). `[measured-here]`, 2026-09-01. See also the retracted "0.883 ceiling" in
[09 — Open problems](09-open-problems.md).

### 24. `--block-size` 128, 2304 and 5632

`--block-size 128` does not start: the engine dies on a DeepGEMM assertion at boot, so 256 is the
smallest legal value. 2304 was the NVFP4-era production value; moving to 256 gained 4.09 % of the
pool. 5632 was considered for C4 and rejected on arithmetic: it pads the KDA/mamba page by about
79 %. `[measured-here, raw lost]`, 2026-08-31. Production is 256.

### 25. `--max-num-batched-tokens`

`8192` was tried after seeing it recommended elsewhere as a concurrency win. Measured: KV 36.21 →
28.38 GiB, pool 5,484,559 → 3,949,381 (**−28 %**), speed signals mixed within ±6 %, and C8 TTFT rose
from 3.70 s to 4.44 s. Trying it **with the KV pin on** locked the head node for 17 minutes with
`No available shared memory broadcast block`, the OOM killer never fired, and the machine had to be
unplugged. `[measured-here, raw lost]`, 2026-08-30. **Stay at 2048; do not reopen this.**

We run 2048; the lab recipe our fork comes from uses 4096. That single-variable A/B was never run on
this stack — see [09 — Open problems](09-open-problems.md).

### 26. Temperature as a speed lever

C1, warm, effort low, two prompts per category:

| Setting | code | json | prose | acceptance |
|---|---|---|---|---|
| T=0 | 55.7 | 56.5 | 23.3 | 66 % / 63 % / 18 % |
| T=0.6, top_p 0.95 | 55.9 | 59.9 | 23.8 | — |
| T=1.0, top_p 0.95 | 52.9 | 58.4 | 22.4 | 60 % / 66 % / 17 % |

Decode tok/s. The effect on speed is ±5 %, inside the noise band — DFlash2's rejection sampling is
robust to temperature. `[measured-here, raw not published]`, 2026-09-03. **Choose temperature for
quality, not for speed.** The model card's own recommendation is 1.0 / 0.95; we run benchmarks at 0
and set it per agent.

### 27. Prefix caching off (`t8`)

Run as a diagnostic while acceptance was low. Acceptance was unchanged at 44–52 %, pool 3,952,522,
code exam 8/12 (within the wobble of that period). Neutral for quality and acceptance, so prefix
caching stays on. `[measured-here, raw not published]`, 2026-09-03.

### 28. Alternative weight loaders

`runai_streamer` was **2–5× slower** on this hardware; `fastsafetensors` does not install on it. One
other loading path silently corrupted the model. We use the fork's `instanttensor` loader for the
main weights and plain safetensors for the draft (the draft loader borrowed a buffer from
`instanttensor` and segfaulted at start — see [`patches/fix-A.md`](../patches/fix-A.md)).
`[measured-here, raw lost]`, 2026-08-31.

### 29. Disabling the FlashInfer autotune warm-up

Considered as "50 free seconds at boot". It is not free: 128 kernel variants are compiled in that
window, and disabling the warm-up moves the compilation into the middle of the service, where a
client waits for it. Left on. `[reported]` plus source reading, 2026-08-31.

### 30. CPU core pinning and DS+HND placement

`--cpuset-cpus` was tried; the cores on this platform are heterogeneous and no gain was demonstrated.
The DS+HND placement flags had no measurable effect. Both settings survive in our launcher because
removing them was never worth a restart, but **the justification is weak and you should not copy them
on faith.** `[measured-here, raw lost]`, 2026-08-31.

---

## Host-level branches

### 31. `vm.swappiness=0`

Applied on all three nodes on 2026-09-02 to stop the head node from paging. It **locked all three
machines** and required a physical power cycle. Reverted to 60, which is what the production setup
runs.

**The general lesson is bigger than the setting:** a `sysctl` change goes to the low-KV test bed
first, on one node, before it goes to a fleet. We had a low-KV test bed available at the time and did
not use it. `[measured-here, raw lost]`.

### 32. TP=2 on two nodes

Evaluated before committing to three nodes, from six published two-node and three-node recipes. All
of these are other people's numbers `[reported]`, 2026-08-29:

| | TP=2 (two nodes) | TP=3 (three nodes) |
|---|---|---|
| Decode, single stream | 22 – 25.6 tok/s | 32 – 35.2 tok/s |
| KV pool | 507 k – 1.22 M tokens | 5.48 M tokens |
| Pinned context | 262 K (with MTP-4) | 1 M |
| TTFT at 1M context | 889 s | prefill ≈ 1,800 tok/s |
| Free memory at 1M | 1.24 GB | comfortable |

**The KV pool gap in that table is the one number we would question if we ran this again.** A 10.8×
pool ratio is far larger than the 1.5× the extra node explains, and the two-node figures came from
setups with a KV pin and different context settings. We never re-measured TP=2 ourselves — the whole
comparison is `[reported]` and it decided a hardware question, which is exactly the kind of decision
our own rule says should rest on two independent sources measured locally. The single-stream figures
did agree across two independent sources, which is why we accepted them as a planning input.

**Why TP=3 anyway.** The cost of TP=3 is about 3 % of wasted compute (attention heads padded 64→66,
shared expert 2048→2112; the 288 experts divide by 3 exactly, so expert parallelism needs no padding
at all) plus a set of loader patches that must be maintained. The gain is concurrency, and this
cluster serves an agent fleet.

### 33. PyTorch 2.14

Reviewed on release. **Not now.** The CUDA side did not change (the default wheel is still CUDA 13.0)
and the fork, the b12x kernels and FlashInfer are all compiled against 2.13; swapping torch
underneath them is a breakage risk for small and indirect gains (lower Dynamo per-call overhead,
compute/communication overlap on by default in Inductor, compile-on-one-rank which would shorten our
boot). Revisit when the fork upgrades torch itself. `[reported]`, 2026-09-03.

---

## Harness lessons

### 34. The two default timeouts that produced meaningless results

Both of these produced *plausible-looking* wrong answers, which is why they are here rather than in a
footnote.

**Needle-in-a-haystack, default 120 s request timeout.** The first run was meaningless: a prefill of
250 K tokens or more cannot finish in 120 s on this cluster, so every large cell would have been
scored as a failure and the run would have read as "long context is broken". Re-run with
`--timeout 3600`, same content and seed: **20/20**, up to 997,952 effective tokens.
`[measured-here]`, results in [`results/needle/`](../results/needle/), 2026-09-03.

**lm-eval, 900 s total timeout.** A full MMLU run died at 81 % (45,499 of 56,168 requests) with
`asyncio.TimeoutError`. The engine was healthy throughout — about 500 requests per minute, zero
errors, health endpoint answering in 0.4 s. The root cause is in the harness: lm-eval queues every
request at once, and aiohttp's `total` timeout counts the time a request spends **waiting in the
connection pool**. Once the queue exceeded 15 minutes, requests began timing out and six retries
later the run collapsed. Fixed with `timeout=36000` (a harness-side timeout only; concurrency and
content unchanged); the re-run completed in 2 h 3 min. Our runner is now pinned at
`num_concurrent=8, max_retries=6, timeout=36000`. `[measured-here]`, results in
[`results/mmlu/`](../results/mmlu/), 2026-09-03.

**The general rule:** before a long harness run, check what the harness will do to a request that is
legitimately slow. A harness default is part of the measurement instrument, and the instrument has to
be verified like everything else.
