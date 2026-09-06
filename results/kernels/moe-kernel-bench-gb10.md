# The MoE kernel is at the DRAM roof at decode, so the FP4 tensor cores have nothing to sell

**One line.** At the shapes this recipe actually runs, the production marlin W4A16 MoE path sits at
**94–99 % of measured DRAM bandwidth** at M = 8 and M = 64, and both FP4 tensor-core paths on this
chip are **1.05–1.07× slower** there; FP4 wins only at prefill-sized M with all experts local
(b12x **1.42× faster** at M = 1,792), and the no-expert-parallel TP-2304 alternative is
**1.16–1.26× slower** at every M **plus 12.5 % more expert bytes** — so it is closed
`[measured-here]`.

Model-free, single GPU, engine untouched. Bench, driver and table generator:
[`bench/moe-kernels/`](../../bench/moe-kernels/). Raw: [`gb10-moebench/`](gb10-moebench/).

## Why this run happened

[09 — Open problems](../../docs/09-open-problems.md) item 7 said that marlin drops the checkpoint's
W4A4 activation scales, that this is the price of the expert maps TP=3 + EP needs, and that **the
cost was unmeasured** because the only comparison arm we had — the b12x MoE backend with expert
parallelism — produces corrupt output (`t4b`, [08 item 7](../../docs/08-what-we-tried.md)). That is
a debt on the front of the recipe, and it stayed open for as long as measuring it meant getting a
broken engine arm to work.

It does not. The kernels can be called directly, inside the production image, with synthetic weights
in the checkpoint layout and no model at all. This page is that measurement. It closes the speed half
of item 7 and leaves the quality half exactly where it was.

The second reason is external: the author of the `cuda-exl3` kernel project asked whether an
sm_121-native NVFP4 MoE path would be worth writing. The last section of this page is the answer in
numbers — what such a kernel would have to beat, per shape.

## Settings

`[measured-here]`, 6 September 2026, 01:52–02:20 Istanbul, one node.

- **GPU:** NVIDIA GB10, cc 12.1, 48 SMs, L2 = 24 MiB. One GPU, not the cluster.
- **Image:** `harem/glm53-lil:t10` — the production image, vLLM fork `0.1.dev0+lil.jovian.9c4dd0548`.
  Every kernel timed is the one production would call.
- **Engine:** not started, not stopped, not touched. Another engine of ours happened to be resident
  and idle on the node throughout; `num_requests_running` and `num_requests_waiting` were 0 and
  `/health` returned 200 at the start and at the end.
- **Container per arm:** `docker run --rm --gpus all --ipc=host --cpuset-cpus 10-14 --memory=16g
  -v <benchdir>:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/moe_kernel_bench.py time …`
- **Weights:** synthetic, Gaussian σ = 0.25, built **expert by expert** and quantised with the
  image's own `scaled_fp4_quant` into the checkpoint layout (packed uint8 + block-16 FP8 e4m3 block
  scale + one global scale per expert), then handed to each backend's own repack helper — marlin
  `prepare_nvfp4_moe_layer_for_marlin`, cutlass `swizzle_blockscale`, b12x `reorder_w1w3_to_w3w1` +
  `swizzle_blockscale` + `convert_sf_to_mma_layout`.
- **Timing:** CUDA events, 20 warm-ups then 3 rounds × 100 calls; the round median is reported.
  Eager throughout, no CUDA graphs, so every arm pays the same launch cost.
- **Two weight sets alternate call by call** so the 24 MiB L2 cannot hold the working set. The
  smallest expert-weight read per call is 0.188 GiB = 8 × L2; the typical call reads 0.6–4.8 GiB.
  The bf16 arm uses one set (two would not fit) and reads 2.4–4.8 GiB per call regardless.
- **M = 8** stands for one stream verifying an 8-token draft, **M = 64** for eight such streams
  (`--max-num-seqs 8`), **M = 1,792** for a prefill-sized batch
  (`--max-num-batched-tokens 2048`).

Reproduce: [`bench/moe-kernels/README.md`](../../bench/moe-kernels/README.md).

### Shapes

| Layout | What it is | local experts | K | N | gate/up | down | top-k |
|---|---|---:|---:|---:|---|---|---|
| **A** | production today: TP=3 + expert parallelism, 96 local of 288 | 96 | 4,096 | 2,048 | K=4,096, N=2×2,048 | K=2,048, N=4,096 | 8 of 288 |
| **B** | the no-EP candidate: intermediate 2,048 → 2,304, sliced by 3 | 288 | 4,096 | 768 | K=4,096, N=2×768 | K=768, N=4,096 | 8 of 288 |

### Forms — how expert parallelism is modelled on layout A

| Form | What it is | Who can run it |
|---|---|---|
| `dense` | "all 8 local": top-8 over the 96 local experts, M rows | every arm |
| `epcompact` | the EP-effective traffic: top-8 over 288, non-local pairs dropped, the surviving (token, expert) pairs run as topk=1 over 96 | every arm |
| `nativeep` | what production runs: global ids over 288 plus `expert_map` (−1 for non-local) | marlin and bf16 only — the FP4 paths refuse expert maps |

`epcompact` is the fair proxy for the FP4 arms because `nativeep` and `epcompact` were **measured**
equivalent on marlin (table T3).

### Arms

| Arm | Kernel | Activations |
|---|---|---|
| `marlin W4A16` | `fused_marlin_moe` — **the production path** | bf16; the checkpoint's FP4 activation scales are dropped |
| `cutlass W4A4` | `run_cutlass_moe_fp4` (`cutlass_fp4_moe_mm`) | FP4, quantised inside the timed region |
| `b12x W4A4` | `flashinfer.fused_moe.B12xMoEWrapper` | FP4, in-kernel BF16 → FP4 quantisation |
| `bf16` | `fused_experts` (triton) over dequantised weights | bf16 — the reference |

Two paths did not run, recorded rather than measured: `FLASHINFER_TRTLLM`
(`_supports_current_device() = False` on sm_121), and `marlin W4A8-FP8` (see side findings).

## The ruler, the throttle check, and the bench checking itself

**Ruler** — a plain bf16 read, 512 × 32,768, four buffers rotating, three rounds, run at the start
of every process and written into its own JSON `[measured-here]`:

| Reading | Value |
|---|---|
| probe run | **240.5 GB/s** (rounds 246.2 / 240.5 / 237.3) |
| across all nine bench processes (27 rounds) | min 220.3 · median 245.3 · max 249.5 GB/s |
| this card, independently, for [docs/07 roofline](../../docs/07-speed.md#roofline-how-close-to-the-hardware-limits) | read-only 243 GB/s, copy 230–246 GB/s |

The ruler is healthy and the spread between processes is about ±5 %. **Read every absolute GB/s
below with a ±5 % band**; the arm-to-arm ratios are unaffected, because each ratio is taken between
arms, not against the ruler.

**Throttle check** — `nvidia-smi` sampled every 5 s into
[`telemetry.csv`](gb10-moebench/telemetry.csv) `[measured-here]`:

| samples | SM clock avg | min | max | card max | peak temp | peak power | samples with `clocks_event_reasons.active` ≠ 0 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 80 (400 s) | 2,430 MHz | 2,398 | 2,476 | 3,003 | 68 °C | 95.1 W | **0** |

No sample was throttle-flagged, so the differences between arms are not a DVFS artefact.

**The bench checking itself.** The marlin/bf16 time ratio at M = 8 is **0.281**. The theoretical byte
ratio of a 4-bit weight plus its fp8 block scale against bf16 is (0.5 + 0.0625) / 2 = **0.28125**.
They agree to three digits, which is the evidence that what is being timed is the expert weight
streaming out of DRAM and not something else.

## Correctness gate

Identical inputs, one dequantised weight bank, the full production K/N/top-k, on a reduced bank of
24 experts (the expert count does not change a single expert's numerics, it only fits in memory).
M = 64. Two references are built from that one bank: **bf16 of the dequantised weights**, and
**emulated W4A4** — the same torch reference with activations NVFP4 quantise-dequantised at *both*
GEMM inputs.

| Layout | Arm | cosine vs bf16 | cosine vs emulated W4A4 | rel-L2 vs bf16 | Gated against | Reading |
|---|---|---:|---:|---:|---|---|
| A | *emulated W4A4 reference* | 0.98765 | 1.0 | 0.15732 | — | (yardstick) |
| A | bf16 (triton) | 0.999992 | 0.98764 | 0.00407 | bf16 | comparable |
| A | marlin W4A16 | 0.999985 | 0.98764 | 0.00543 | bf16 | **comparable** |
| A | cutlass W4A4 | 0.98766 | **0.99805** | 0.15711 | emulated W4A4 | **comparable** |
| A | b12x W4A4 | 0.98664 | 0.98564 | 0.16311 | emulated W4A4 | **comparable — see below** |
| B | *emulated W4A4 reference* | 0.98753 | 1.0 | 0.15791 | — | (yardstick) |
| B | bf16 (triton) | 0.999992 | 0.98753 | 0.00407 | bf16 | comparable |
| B | marlin W4A16 | 0.999985 | 0.98752 | 0.00543 | bf16 | **comparable** |
| B | cutlass W4A4 | 0.98752 | **0.99804** | 0.15797 | emulated W4A4 | **comparable** |
| B | b12x W4A4 | 0.98609 | 0.98512 | 0.16692 | emulated W4A4 | **comparable — see below** |

**A cosine-0.99-against-bf16 gate is not reachable by any W4A4 path on this data, and that is the
first finding, not an excuse.** The exact emulated W4A4 reference itself only reaches 0.9876.
Synthetic Gaussian activations are near the worst case for FP4 activation quantisation. So the W4A4
arms are gated against the emulated reference, and cutlass passes that gate outright: 0.998 cosine,
rel-L2 0.1571 against the reference's 0.1573.

**The b12x arm is flagged by the script and passed by us, and both facts are published.** Its
mechanical `VERDICT` field in [`gate-A.json`](gb10-moebench/gate-A.json) reads **`NOT COMPARABLE`**,
because 0.9856 is under the flat 0.99 threshold the script applies. Our reading is that it is in the
same error class as the reference — rel-L2 0.163 against 0.157, **+4 %** — and not a corruption:
`t4b`-style corruption put the cosine in the 0.1–0.7 band, two orders of magnitude away from this.
The difference is that b12x scales its FC2 input **dynamically per block** rather than with a
pre-computed global scale — a different but legitimate W4A4 rounding. **If you run the gate, expect
that flag**; read the two cosines, not the verdict field.

**The cost side of W4A4, recorded because it is not free.** At the MoE layer, W4A4's numerical noise
is rel-L2 **0.157** against marlin W4A16's **0.0054** — about **29× noisier**. That is a direction,
not a quality verdict: real activations are far more structured than Gaussian, and what W4A4 does to
MMLU or the code exam has **not** been measured `[not tested]`.

## Timing

`pairs` = (token, expert) pairs in the call; `experts` = experts actually touched. `x marlin` below
1 means faster than marlin. Microseconds are round medians.

### T1. Layout A, all-8-local form (96 local experts, K=4,096, N=2,048, top-8 of 96)

| M | routing | pairs | experts | marlin W4A16 us | x marlin | cutlass W4A4 us | x marlin | b12x W4A4 us | x marlin | bf16 us | x marlin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | 64 | 47 | 2806 | 1.00 | 3090 | 1.10 | 2992 | 1.07 | 9999 | 3.56 |
| 8 | zipf | 64 | 36 | 2169 | 1.00 | 2383 | 1.10 | 2290 | 1.06 | 7706 | 3.55 |
| 64 | uniform | 512 | 96 | 5813 | 1.00 | 6331 | 1.09 | 6170 | 1.06 | 20804 | 3.58 |
| 64 | zipf | 512 | 82 | 5059 | 1.00 | 5444 | 1.08 | 5286 | 1.04 | 18081 | 3.57 |
| 1792 | uniform | 14336 | 96 | 13443 | 1.00 | 12488 | 0.93 | **9482** | **0.71** | 46029 | 3.42 |
| 1792 | zipf | 14336 | 96 | 13336 | 1.00 | 12645 | 0.95 | **10079** | **0.76** | 40327 | 3.02 |

### T2. Layout A, EP-effective form — the production traffic

| M | routing | pairs | experts | marlin W4A16 us | x marlin | cutlass W4A4 us | x marlin | b12x W4A4 us | x marlin | bf16 us | x marlin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | 24 | 20 | **1215** | 1.00 | 1366 | 1.12 | 1300 | 1.07 | 5240 | 4.31 |
| 8 | zipf | 54 | 28 | 1709 | 1.00 | 1891 | 1.11 | 1809 | 1.06 | 6076 | 3.55 |
| 64 | uniform | 192 | 86 | **5145** | 1.00 | 5607 | 1.09 | 5500 | 1.07 | 20868 | 4.06 |
| 64 | zipf | 410 | 85 | 5147 | 1.00 | 5657 | 1.10 | 5493 | 1.07 | 20557 | 3.99 |
| 1792 | uniform | 4854 | 96 | **6897** | 1.00 | 8894 | 1.29 | 7273 | 1.05 | 27944 | 4.05 |
| 1792 | zipf | 11457 | 96 | 11789 | 1.00 | 12533 | 1.06 | **10083** | **0.86** | 37384 | 3.17 |

> **How to read the `zipf` rows here.** The skew is over expert **ids**, and expert parallelism gives
> rank 0 the ids 0–95 — exactly where the Zipf mass sits. So on this form `zipf` is a **worst-case
> rank-imbalance** reading, not a hot-expert reading: 11,457 of 14,336 pairs = 80 % on one rank
> against 33 % in balance. The balanced reading is the `uniform` column. On `dense` and on layout B
> every expert is local, so there `zipf` is a clean hot-expert reading.

### T3. Marlin: does a real expert map cost anything against the proxy form?

| M | routing | marlin native-EP (288 ids + `expert_map`) us | marlin ep-compacted us | ratio |
|---|---|---|---|---|
| 8 | uniform | 1218 | 1215 | 1.00 |
| 8 | zipf | 1710 | 1709 | 1.00 |
| 64 | uniform | 5172 | 5145 | 1.01 |
| 64 | zipf | 5152 | 5147 | 1.00 |
| 1792 | uniform | 7312 | 6897 | 1.06 |
| 1792 | zipf | 11603 | 11789 | 0.98 |

**The expert map is effectively free to marlin** — within 1 % at decode sizes, 6 % at M = 1,792.
There is no line item called "what EP costs marlin", and `epcompact` is a fair proxy for the arms
that cannot take a map.

### T4. Layout B — no expert parallelism, intermediate 2,304 / 3 (288 local, K=4,096, N=768)

| M | routing | pairs | experts | marlin W4A16 us | x marlin | cutlass W4A4 us | x marlin | b12x W4A4 us | x marlin | bf16 us | x marlin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | 64 | 57 | 1307 | 1.00 | 1471 | 1.13 | 1467 | 1.12 | 5156 | 3.95 |
| 8 | zipf | 64 | 38 | 895 | 1.00 | 1024 | 1.14 | 970 | 1.08 | 5074 | 5.67 |
| 64 | uniform | 512 | 238 | 5354 | 1.00 | 5849 | 1.09 | 5989 | 1.12 | 19076 | 3.56 |
| 64 | zipf | 512 | 168 | 3818 | 1.00 | 4196 | 1.10 | 4250 | 1.11 | 13545 | 3.55 |
| 1792 | uniform | 14336 | 288 | 8718 | 1.00 | 12442 | 1.43 | 8708 | 1.00 | 31016 | 3.56 |
| 1792 | zipf | 14336 | 288 | 9638 | 1.00 | 12406 | 1.29 | 9222 | 0.96 | 31057 | 3.22 |

**The FP4 advantage disappears entirely on layout B.** 288 expert groups at N = 768 destroys the
"many rows per expert" regime the FP4 kernels want; b12x only draws level at M = 1,792 (1.00 / 0.96)
and is 8–12 % behind at small M.

## Bandwidth and TFLOPS

TFLOPS is **logical** FLOP — `pairs × 6 × N × K`, the work the dequantised arithmetic represents.
GB/s is the expert weight actually touched in that call divided by the time.

### T5. Layout A, all-8-local

| M | routing | marlin GB/s | marlin TFLOPS | cutlass GB/s | cutlass TFLOPS | b12x GB/s | b12x TFLOPS | bf16 GB/s | bf16 TFLOPS |
|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | **237** | 1.1 | 215 | 1.0 | 222 | 1.1 | 237 | 0.3 |
| 8 | zipf | 235 | 1.5 | 214 | 1.4 | 222 | 1.4 | 235 | 0.4 |
| 64 | uniform | 234 | 4.4 | 215 | 4.1 | 220 | 4.2 | 232 | 1.2 |
| 64 | zipf | 229 | 5.1 | 213 | 4.7 | 220 | 4.9 | 228 | 1.4 |
| 1792 | uniform | 101 | 53.7 | 109 | 57.8 | 143 | **76.1** | 105 | 15.7 |
| 1792 | zipf | 102 | 54.1 | 108 | 57.1 | 135 | 71.6 | 120 | 17.9 |

### T6. Layout A, EP-effective

| M | routing | marlin GB/s | marlin TFLOPS | cutlass GB/s | cutlass TFLOPS | b12x GB/s | b12x TFLOPS | bf16 GB/s | bf16 TFLOPS |
|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | 233 | 1.0 | 207 | 0.9 | 218 | 0.9 | 192 | 0.2 |
| 8 | zipf | 232 | 1.6 | 210 | 1.4 | 219 | 1.5 | 232 | 0.5 |
| 64 | uniform | 237 | 1.9 | 217 | 1.7 | 221 | 1.8 | 207 | 0.5 |
| 64 | zipf | 234 | 4.0 | 213 | 3.6 | 219 | 3.8 | 208 | 1.0 |
| 1792 | uniform | 197 | 35.4 | 153 | 27.5 | 187 | 33.6 | 173 | 8.7 |
| 1792 | zipf | 115 | 48.9 | 108 | 46.0 | 135 | 57.2 | 129 | 15.4 |

### T7. Layout B

| M | routing | marlin GB/s | marlin TFLOPS | cutlass GB/s | cutlass TFLOPS | b12x GB/s | b12x TFLOPS | bf16 GB/s | bf16 TFLOPS |
|---|---|---|---|---|---|---|---|---|---|
| 8 | uniform | 232 | 0.9 | 206 | 0.8 | 206 | 0.8 | 209 | 0.2 |
| 8 | zipf | 225 | 1.4 | 197 | 1.2 | 208 | 1.2 | 141 | 0.2 |
| 64 | uniform | 236 | 1.8 | 216 | 1.6 | 211 | 1.6 | 236 | 0.5 |
| 64 | zipf | 234 | 2.5 | 212 | 2.3 | 210 | 2.3 | 234 | 0.7 |
| 1792 | uniform | 175 | 31.0 | 123 | 21.8 | 176 | 31.1 | 175 | 8.7 |
| 1792 | zipf | 159 | 28.1 | 123 | 21.8 | 166 | 29.3 | 175 | 8.7 |

**This is the whole argument in one place.** At M = 8 and M = 64, marlin reads 225–237 GB/s against a
240.5 GB/s ruler — **94–99 % of DRAM**. There is no idle arithmetic for FP4 tensor cores to sell,
and the W4A4 activation-quantisation kernels (`scaled_fp4_experts_quant`,
`silu_and_mul_scaled_fp4_experts_quant`) give back **4–13 %** of that band to produce something the
memory system cannot deliver faster. At M = 1,792 the regime flips to compute-bound — marlin falls to
101 GB/s and climbs to 54 TFLOPS — and there FP4 genuinely wins: b12x reaches **76.1 TFLOPS**, about
78 % of this card's measured BF16 matmul ceiling of 97 TFLOPS
([docs/07](../../docs/07-speed.md#roofline-how-close-to-the-hardware-limits)) and far below any FP4
tensor-core ceiling.

### T8. Round-to-round spread

| layout | arm | max round-to-round spread | median spread |
|---|---|---|---|
| A | bf16 | 0.8 % | 0.3 % |
| A | b12x W4A4 | 1.4 % | 0.5 % |
| A | marlin W4A16 | 2.6 % | 0.5 % |
| A | cutlass W4A4 | 1.9 % | 0.6 % |
| B | bf16 | 1.1 % | 0.5 % |
| B | b12x W4A4 | 2.2 % | 1.1 % |
| B | marlin W4A16 | 1.0 % | 0.8 % |
| B | cutlass W4A4 | 7.6 % | 0.7 % |

Median spread ≤ 1.1 %. The 4–29 % differences reported above are well clear of it; **differences
under 5 % — such as marlin's 5 % lead over b12x at M = 1,792 EP-effective — should be read as close
to noise.**

### T9. Weight banks

| layout | arm | weight sets | raw bank / set | resident after prep | peak |
|---|---|---|---|---|---|
| A | bf16 | 1 | 1.27 GiB | 4.50 GiB | 5.78 GiB |
| A | marlin W4A16 | 2 | 1.27 GiB | 2.53 GiB | 8.20 GiB |
| A | cutlass W4A4 | 2 | 1.27 GiB | 2.53 GiB | 3.19 GiB |
| A | b12x W4A4 | 2 | 1.27 GiB | 2.53 GiB | 3.42 GiB |
| B | bf16 | 1 | 1.42 GiB | 5.06 GiB | 6.49 GiB |
| B | marlin W4A16 | 2 | 1.42 GiB | 2.85 GiB | 9.23 GiB |
| B | cutlass W4A4 | 2 | 1.42 GiB | 2.85 GiB | 3.46 GiB |
| B | b12x W4A4 | 2 | 1.42 GiB | 2.85 GiB | 3.85 GiB |

The marlin peak is the repack, not the steady state. The routed-expert weight per set is the
**+12.52 %** figure used below: 1.424 GiB on layout B against 1.266 GiB on layout A.

## Verdict 1 — marlin costs this recipe nothing at decode

In our own production shape (layout A, EP-effective traffic, `uniform` routing), the best FP4
tensor-core path is **7 % slower than marlin at M = 8, 7 % at M = 64 and 5 % at M = 1,792**. The single
place FP4 leads on this form is the worst-case rank-imbalance row, M = 1,792 `zipf`, where b12x is
**17 % faster**. On the all-local form the FP4 win at prefill size is real and large — b12x
**1.42×** at M = 1,792 `uniform` — but production does not run that form at three ranks.

The measured reason is in T5–T7, not a theory: the kernel is at the memory roof at decode sizes.
This is consistent with, and sharper than, the step-level estimate in
[docs/07 roofline](../../docs/07-speed.md#roofline-how-close-to-the-hardware-limits), which put
decode with the draft at 80–85 % of bandwidth and said "faster kernels buy little there".

**What it does not close:** the *quality* half of
[09 item 7](../../docs/09-open-problems.md#7-marlin-drops-the-checkpoints-activation-scales-the-speed-cost-is-now-measured-the-quality-cost-is-not).
Nothing here says what running the experts A16 costs the model. It says that fixing it would also
cost speed.

## Verdict 2 — the no-EP TP-2304 arm is closed

At equal token traffic — same M, same top-8-of-288 routing — a rank on layout A reads its own 96
experts and processes one third of the pairs; on layout B it reads all 288 experts at one third of
the width and processes all of them. `uniform` routing, the balanced reading:

### T10 (uniform)

| M | Layout A (EP) marlin | Layout B marlin | Layout B best FP4 (b12x) | B best / A marlin |
|---|---|---|---|---|
| 8 | **1,215 us** | 1,307 us (1.08×) | 1,467 us | **1.21× slower** |
| 64 | **5,145 us** | 5,354 us (1.04×) | 5,989 us | **1.16× slower** |
| 1792 | **6,897 us** | 8,718 us (1.26×) | 8,708 us | **1.26× slower** |

And the bill:

| Item | Measured | Source |
|---|---|---|
| routed-expert weight bytes, B against A | **+12.52 %** (1.424 vs 1.266 GiB per weight set) | this bench, T9 `[measured-here]` |
| rank footprint of the `flashinfer_b12x` MoE backend against marlin, in the engine | +9.4 GiB per rank (72.89 vs 63.48 GiB) | NVFP4-era engine work `[measured-here, raw not published]` |
| what that comes out of | the KV pool — the scarcest resource in this recipe ([05 — Memory ladder](../../docs/05-memory-ladder.md)) | |

**Slower at every M, and it wants memory the KV pool does not have.** The branch does not justify an
engine attempt; it is closed. The one thing this bench cannot see is on its side of the ledger and
is stated in "What this does not say" below.

## Side findings

**1. There is no marlin W4A8 door.** "Keep marlin's expert maps but give it fp8 activations" is not
an option that exists: the image's `marlin_utils_fp4.py` refuses NVFP4 weights with a 1-byte
activation dtype in five places, including **line 363**, in the very MoE prepare path we would have
to go through:

```text
input_dtype = get_marlin_input_dtype(prefix="")
if input_dtype is not None and input_dtype.itemsize == 1:
    raise RuntimeError("NVFP4 weight + INT8/FP8 activation is not supported.")
```

Our `marlin-w4a8fp8` arm never even reached that line — it failed earlier, constructing the fp8
quantisation method outside a cached compilation config
([`gate-A-first-attempt.log`](gb10-moebench/gate-A-first-attempt.log)) — but the source is
unambiguous and the arm is kept in the bench so the refusal stays reproducible. `[measured-here]`.

**2. b12x is not guessing on this GPU — and its own measured profile chooses W4A16 under EP.** The
image ships `b12x/policy/_profiles/data/nvidia.gb10.48sm.json.gz`, whose target is
`{compute_capability: [12,1], product_name: "nvidia gb10", sm_count: 48}` — this exact GPU. It holds
20 components and its `moe.decode` component is a real decision tree (backend `micro` / `dynamic`,
`tile_m`, route mode). But its `moe.ep_moe` component is a single leaf:

```json
{"config": {"backend": "w4a16"}, "name": "measured-production-implementation"}
```

**Under expert parallelism, b12x's own measured GB10 profile selects W4A16 — the same class as
marlin.** That is the vendor arriving at our answer independently. `[measured-here]`. (The profile is
not exhaustive: our engine boot logs carry `b12x policy fallback: attention.gdn is using a heuristic
on nvidia gb10 … because profile 'nvidia.gb10.48sm' does not cover the query` for a different
component. It exists and it is measured; it just does not cover every shape we ask for.)

**3. Generating our own b12x profile is not possible in this image.** `generate_gpu_profile.py`
fails on `--list-components` with `ModuleNotFoundError: No module named 'benchmarks.benchmark_qsa'`
— the tool depends on a `benchmarks` module that is not packaged in the image. Profile generation is
a separate source-tree job, and given finding 2 it would not be expected to change the EP verdict.
`[measured-here]`.

**4. W4A4 is about 29× noisier than W4A16 at the MoE layer** on synthetic Gaussian activations
(rel-L2 0.157 against 0.0054). Direction only — see the correctness gate.

## What this bench does not say

- **What share of a real step the MoE occupies.** Every ratio here is *inside one kernel call*. If
  the MoE is 10 % of a step, the 29 % FP4 win at all-local prefill is 3 % of that step. That is a
  profiler measurement and it has not been run here. `[not tested]`
- **Communication.** Layout B has no all-to-all dispatch/combine — it would collapse to a single
  all-reduce. This bench is **single GPU** and cannot see that at all. Whether the communication
  side could win back the 16–26 % kernel-side loss is **unmeasured**; the +12.5 % weight bytes and
  the +9.4 GiB per rank stand regardless of it. `[not tested]`
- **Quality.** rel-L2 0.157 is synthetic Gaussian at one layer. What W4A4 does to MMLU, the code
  exam or the correctness probe on the real model has not been measured. If W4A4 is ever adopted,
  that is a separate gate. `[not tested]`
- **Real routing.** `uniform` and `zipf(s=1)` over expert ids. The real router's token-expert
  distribution was not measured. The marlin ↔ FP4 ratio is nearly identical under both
  (1.10 ↔ 1.10, 1.09 ↔ 1.08, 1.07 ↔ 1.06), which is evidence that the verdict is insensitive to
  the distribution, not proof of it.
- **Synthetic weights.** Byte count and shape are the real ones, so the speed figures stand; the
  real checkpoint's within-block dynamics differ, so nothing here binds on quality.
- **CUDA graphs and launch counts.** All arms measured eager, so all pay the same launch cost and
  the ratios are comparable; no `torch.profiler` split/epilogue launch counting was done.
- **Three rounds is three rounds.** Median spread ≤ 1.1 %, all rounds are in every JSON.

## What a custom sm_121 NVFP4 MoE path would have to beat

Numbers only. The floor below is the time it would take to read the expert weight the call actually
touches, once, at the 240.5 GB/s ruler — derived from the measured `touched_gib` in the raw JSONs,
not separately measured.

### D1 (derived). Marlin against its own DRAM-time floor, `uniform` routing

| Form | M | pairs | experts | touched | marlin us | DRAM floor us | attained |
|---|---:|---:|---:|---:|---:|---:|---:|
| A EP-effective | 8 | 24 | 20 | 0.264 GiB | 1,215 | 1,179 | **97 %** |
| A EP-effective | 64 | 192 | 86 | 1.134 GiB | 5,145 | 5,063 | **98 %** |
| A EP-effective | 1792 | 4,854 | 96 | 1.266 GiB | 6,897 | 5,652 | 82 % |
| A all-local | 8 | 64 | 47 | 0.620 GiB | 2,806 | 2,768 | **99 %** |
| A all-local | 64 | 512 | 96 | 1.266 GiB | 5,813 | 5,652 | **97 %** |
| A all-local | 1792 | 14,336 | 96 | 1.266 GiB | 13,443 | 5,652 | 42 % |
| B | 8 | 64 | 57 | 0.282 GiB | 1,307 | 1,259 | 96 % |
| B | 64 | 512 | 238 | 1.177 GiB | 5,354 | 5,255 | 98 % |
| B | 1792 | 14,336 | 288 | 1.424 GiB | 8,718 | 6,358 | 73 % |

The three numbers to beat, on the production form (layout A, EP-effective, `uniform`):

| M | Best measured today | By whom | Headroom left to the DRAM floor |
|---:|---:|---|---|
| 8 | **1,215 us** | marlin W4A16 | 36 us — **3 %** |
| 64 | **5,145 us** | marlin W4A16 | 82 us — **2 %** |
| 1792 | **6,897 us** | marlin W4A16 | 1,245 us — **18 %** |

On the all-local form at M = 1,792, the number to beat is **9,482 us** (b12x W4A4, 143 GB/s,
76.1 TFLOPS), against a 5,652 us floor — **40 % headroom**, and the only place on this page where a
large one exists. Marlin there is 13,443 us at 53.7 TFLOPS.

For context on the ceilings: this card measures 240.5 GB/s on the ruler, 97 TFLOPS BF16 matmul and
199 TFLOPS fp8 `_scaled_mm` at 8,192²
([docs/07](../../docs/07-speed.md#roofline-how-close-to-the-hardware-limits)). The best FP4 MoE
figure measured anywhere on this page is 76.1 TFLOPS.

## Raw

[`gb10-moebench/`](gb10-moebench/) — 64 KB, **unedited**. Nothing in it needed scrubbing: every path
it contains is inside the container. The one thing to know before reading it is that the script was
called `moebench.py` when these files were written and is published here as `moe_kernel_bench.py`;
the logs and the `--out` arguments still carry the old name.

| File | What it is |
|---|---|
| `A-*.json`, `B-*.json` | one per arm and layout: shapes, weight-bank size, the process's own ruler reading, memory after prep and peak, then one record per (form, M, routing) with all three rounds, the median, the spread, touched bytes, GB/s and TFLOPS |
| `A-fi-b12x-w4a4-epcompact.json` | the b12x EP-effective form, re-run in its own process after the failure described in `A-fi-b12x-w4a4.log` |
| `gate-A.json`, `gate-B.json` | the correctness gate, including the script's mechanical `VERDICT` field and the pairwise cosines between all four arms |
| `gate-A-first-attempt.log` | the earlier gate attempt, kept because it is the only capture of the `marlin-w4a8fp8` arm failing and of a b12x construction error since fixed. **Its b12x rows are superseded by `gate-A.json`** |
| `A-*.log`, `B-*.log` | per-arm stdout of the timing runs, one line per measurement |
| `A-fi-b12x-w4a4.log` | includes the two `epcompact` failures and the `cudaErrorInvalidAddressSpace` that killed that process |
| `RUNALL.log` | the driver's own transcript: every arm, its start time, return code and per-measurement lines, plus host `MemAvailable` after each arm |
| `telemetry.csv` | `unix_ts, temp_C, power_W, sm_clock, max_sm_clock, gpu_util, clocks_event_reasons_active, MemAvailable_kB` every 5 s |

Regenerate T1–T10 from these files:

```bash
python3 bench/moe-kernels/make-tables.py results/kernels/gb10-moebench
```
