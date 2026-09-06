# The FP4 crossover exists, and it sits above the traffic this recipe runs

**One line.** Ten batch sizes instead of three, a memory ceiling measured three ways, and the FP4
path's plumbing timed separately: at every decode and concurrency point (M = 8 / 64 / 128 / 256)
marlin W4A16 sits at **91–96 % of a 245.9 GB/s ceiling** and **even a zero-overhead FP4 GEMM measures
1.03–1.07× slower** there, so a custom kernel could win at most 4–9 %; the FP4 path overtakes marlin
only from **M = 1,024 with all experts local** — a form three ranks with expert parallelism do not
run — and in the production EP form at prefill it comes out **level, not ahead** (GEMM-only 0.98×)
`[measured-here]`.

Model-free, single GPU, engine untouched. Sweep, driver and table generator:
[`bench/moe-kernels/crossover/`](../../bench/moe-kernels/crossover/). Raw:
[`gb10-crossover/`](gb10-crossover/).

## Why this run happened

[`moe-kernel-bench-gb10.md`](moe-kernel-bench-gb10.md) measured three batch sizes — M = 8, 64 and
1,792 — and concluded that the production marlin W4A16 MoE path has nothing to give up to this
chip's FP4 tensor cores. The author of the `cuda-exl3` kernel project, who had asked for that
measurement, did not accept it:

> b12x is not perfect for sm121; an sm_121-native, NVFP4-native path would be much better both at
> prefill and in tok/s under concurrency. Set the earlier bias aside and measure again.

Three of the objections were checkable against the earlier bench's own source, and one of them was
right in a way that mattered. This page is the re-measurement. It was built **wanting the theory to
come out true**: the measurement design, the thresholds and the verdict rule were written down
before the run and are reproduced below unchanged. A verdict rule written after seeing the numbers
cannot answer a charge of bias, so this one was not.

The short answer is that the earlier verdict survives, on much harder evidence, and that the earlier
bench owed the author one correction — at prefill sizes, 38 % of what it charged to "the FP4 kernel"
was not the kernel.

---

# Part 1 — The pre-registered design

Everything in this part was written before the run.

## Settings

`[measured-here]`, 6 September 2026, 05:12–05:27 Istanbul, one node. Container logs are UTC.

- **GPU:** NVIDIA GB10, cc 12.1 (sm_121), 48 SMs, L2 = 24 MiB, CUDA 13.0, torch 2.13. One GPU, not
  the cluster.
- **Image:** `harem/glm53-lil:t10` — the production image. Every kernel timed is one production
  would call.
- **Engine:** not started, not stopped, not touched. Another engine of ours was resident and
  **idle** on the node throughout: `num_requests_running` and `num_requests_waiting` were 0 and
  `/health` returned 200 before and after, and it was sent one warm-up request at the end.
- **Container per arm:** `docker run --rm --gpus all --ipc=host --cpuset-cpus 10-14 --memory=6g
  -v <benchdir>:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/xsweep.py …`
- **Weights:** synthetic, Gaussian σ = 0.25, built expert by expert and quantised with the image's
  own `scaled_fp4_quant` into the checkpoint layout, then handed to each backend's own repack helper
   — the same construction as the earlier bench.
- **Timing:** CUDA events, 20 warm-ups then 3 rounds × 50 calls, round median. Eager throughout, so
  every arm pays the same launch cost.
- **Two weight banks alternate call by call** so the 24 MiB L2 cannot hold the working set; the
  smallest per-call expert-weight read is 0.26 GiB = 11 × L2, typically 0.6–1.3 GiB. The bf16 arm
  uses one bank (two do not fit) and reads 2.4–4.8 GiB per call regardless.
- **Shape (layout A only, the production one):** 96 local experts of 288, hidden K = 4,096, per-rank
  intermediate N = 2,048 (gate-up `[E, 2N, K]`, down `[E, K, N]`), top-8.
- **Throttle:** `clocks_event_reasons.active = 0x0` in **153 of 153** samples; SM clock mean
  2,426 MHz (min 2,392, max 2,457), peak 72 °C and 98.7 W.
- **Round-to-round spread** across every timed point: median **0.52 %**, max **4.17 %**.

Reproduce: [`bench/moe-kernels/crossover/README.md`](../../bench/moe-kernels/crossover/README.md).

## The three rulers

The earlier verdict rested on one sentence — *marlin is at 92–99 % of the memory ceiling, so there
is no idle arithmetic for FP4 to sell* — and that ceiling had been measured **one way**:
`torch.Tensor.sum()` over four rotating 32 MiB bf16 buffers, median 240.5 GB/s. Two things were
wrong with resting a verdict on it. `.sum()` is a torch kernel, not an instrument: whether it issues
vectorised loads is not our choice, and a ruler reading 5–10 % low would have opened exactly that
much room for the theory. And a 32 MiB buffer is only 1.33 × L2, nowhere near the 0.6–4.8 GiB an
expert bank streams.

So the ceiling is rebuilt three independent ways and **the best of them is the ceiling**:
`torch.sum()` (at 32 MiB and at 2 GiB, to separate method from buffer size), `cudaMemcpyD2D` at
2 GiB read as (2 × size) / time, and a hand-written `__ldg` / `uint4` streaming reader at 2 GiB,
best over a block and thread sweep. The LPDDR5X datasheet peak (273 GB/s) is context and is **not**
used as a ceiling.

## The sweep

- **M ∈ {8, 16, 32, 64, 128, 256, 512, 1024, 1792, 4096}** — ten points, so a crossover can be
  located rather than assumed.
- **Arms:** marlin W4A16 (production) · vLLM cutlass W4A4 · FlashInfer b12x W4A4 · bf16 reference.
- **Forms:** `dense` = **all-8-local**, top-8 over the 96 local experts; `epcompact` =
  **EP-effective**, top-8 over 288 with the non-local pairs dropped and the survivors run as
  topk = 1. The second is the traffic an expert-parallel rank actually sees, and it is the
  production form. Both are defined and justified in
  [`bench/moe-kernels/README.md`](../../bench/moe-kernels/README.md).
- **Routing:** `uniform` and `zipf` s = 1. Verdicts are read from `uniform`. On `epcompact`, `zipf`
  is a **rank-imbalance** reading — expert parallelism gives rank 0 exactly the ids the Zipf mass
  sits on — not a hot-expert reading, and it is reported as the pathological case it is.
- **Crossover M** = the first M at which the best FP4 path beats marlin by **more than 5 %**,
  reported per form and per routing.

## The GEMM-only bound

This is the part the objection was owed. `run_cutlass_moe_fp4` does six `torch.empty` metadata
allocations, `get_cutlass_moe_mm_data`, two `shuffle_rows`, two activation quantisations and a final
`(c3 * topk_w).sum(dim=1)` combine on every call. The earlier bench charged all of it to "cutlass
W4A4" and compared that against marlin. But the question asked was what the FP4 *tensor cores* would
buy, and a fused custom kernel would not pay most of that plumbing.

So the FP4 path is timed with the plumbing removed: activations pre-quantised, routing metadata
pre-built, no row shuffle, no epilogue combine. What remains is the two grouped FP4 tensor-core
GEMMs — **the floor no fused kernel can go below**, because it must still stream the same weights
and do the same MACs. `act-quant` alone and `metadata + combine` alone are timed separately, so the
plumbing is priced rather than argued about.

b12x has no pre-quantised entry point in its public API (`run()` takes bf16 `x` only), so the FP4
GEMM floor is taken from the cutlass grouped GEMM and labelled that way.

## The roofline

```text
W            = experts touched x measured bytes per expert
A            = (rows_in + rows_out) x K x 2        (bf16 in once, out once)
t_mem        = (W + A) / ceiling
FLOPs        = pairs x 6 x N x K                   (logical, dequantised math)
t_fp4        = FLOPs / 500e12                      (datasheet dense FP4 tensor peak, sm_121)
t_bf16       = FLOPs / 97.3e12                     (our measured BF16 tensor throughput)
roofline_fp4 = max(t_mem, t_fp4)
```

`marlin / roofline_fp4` is **the honest ceiling** at a point: the most any custom kernel, of any
design, could win there.

## The operating points

| Point | M | What it is |
|---|---:|---|
| C1 | 8 | one stream verifying a 7-token DFlash2 draft |
| C8 | 64 | eight concurrent streams |
| C16 | 128 | sixteen concurrent streams |
| C32 | 256 | thirty-two concurrent streams |
| prefill | 1,792 | a prefill chunk under `--max-num-batched-tokens 2048` |

M = 4,096 is **not** an operating point — one forward pass never sees it at MNBT 2,048 — and is
measured only to show the shape of the curve.

## The verdict criteria

At an operating point the theory is **SUPPORTED** if and only if:

- **Criterion A:** the GEMM-only FP4 path beats marlin by more than 10 % (`gemm-only / marlin < 0.90`),
  **or**
- **Criterion B:** `roofline_fp4` is more than 1.25× faster than measured marlin
  (`marlin / roofline_fp4 > 1.25`).

Otherwise **NOT SUPPORTED**. No softening; the verdict is written out point by point.

---

# Part 2 — Results

## What was claimed before the run, and what the measurement said

Four flaw claims and two hypotheses were recorded against the earlier bench before this one ran,
from reading its source. Here is how each came out. `[measured-here]`

| # | Claim made before the run | Measured verdict | Effect on the earlier conclusion |
|---|---|---|---|
| a | the ruler was built one way and could read 5–10 % low | **NOT CONFIRMED.** Three independent methods give 238.6 / 245.9 / 239.5 GB/s, agreeing inside 3 %. The earlier 240.5 is only **2.2 %** below the best | negligible: "marlin at 92–99 % of the ruler" becomes "**91–96 % of a three-way-confirmed ceiling**" |
| b | the b12x wrapper was built at ≥ 2,048 tokens for every M, so the arm ran mis-sized | **PARTLY TRUE, BUT FAITHFUL TO PRODUCTION.** Production builds the wrapper once from a fixed `max_num_batched_tokens` too. Per-batch sizing is worth **8.8–9.6 %** at M = 128/256 all-8-local and nothing (≤ 2 %) in the production EP form | changes no verdict — b12x is still 6 % behind marlin at matched size — but it is a real, cheap improvement, and it is written up as one |
| c | nothing was measured between M = 64 and M = 1,792, so the crossover was never looked for | **CONFIRMED, AND IT MATTERED.** In the all-8-local form marlin has a **cliff between M = 512 and M = 1,024** (6,619 → 10,355 us, +56 %; round spread 0.2–1.9 %, so not noise). The FP4 GEMM does not have it | the earlier M grid stepped over the cliff: the all-8-local crossover is **M = 1,024**, not M = 1,792 |
| d | the W4A4 plumbing was charged to the GEMM | **CONFIRMED, AND THIS IS THE LARGEST CORRECTION.** At M = 1,792 all-8-local, **38 %** of the cutlass path's wall time is outside the GEMM (2,052 us act-quant + 2,673 us metadata/combine of 12,461 us) | the earlier line *"cutlass is 29 % slower than marlin at prefill"* is **not a kernel verdict**: at the same point GEMM-only is **0.98×**, level with marlin |
| e | marlin inflates its scales to bf16, so its byte accounting was wrong | **DISPROVED**, before the run from the source and again in the run by measurement: all three quantised arms carry **14,155,78x B per expert**, 1.0000× the model | the earlier byte accounting was right |
| f | the earlier bench bypassed b12x's own profile layer | **DISPROVED.** Production calls the same `B12xMoEWrapper` | no production layer was skipped; one sentence in the earlier page is weakened, see below |

### The one-sentence verdict of the earlier bench

**It stands, and now on much harder evidence.** At the decode and concurrency points (M = 8, 64,
128, 256) the FP4 path is slower than marlin **even with activation quantisation, row shuffling,
metadata and epilogue combine removed entirely** (GEMM-only / marlin = **1.03–1.07×**). There is no
"fix the plumbing and FP4 wins" door: the bare FP4 GEMM is itself about 5 % slower in that regime,
218 GB/s against 234 GB/s on identical bytes.

**Two things the earlier bench got wrong.** In the all-8-local form the crossover is at M = 1,024 and
the honest ceiling at M = 1,792 is **2.33×**, which the earlier M grid missed and then understated as
a "29 % gain" — but that is not our traffic; in production every rank sees the EP-effective form. And
marlin's 29 % lead at prefill was **plumbing cost, not kernel superiority**: in the production form
GEMM-only is 0.98×.

**The author's theory is NOT SUPPORTED at four of five operating points, and SUPPORTED at the fifth
(prefill) only in the non-EP form — in the production form it is not supported there either.**

## R1. Memory ceiling, measured three ways

`[measured-here]`

| ruler | method | buffer | GB/s | rounds |
|---|---|---|---|---|
| a_torch_sum_small | torch .sum(), 4 x 32 MiB bf16 (the earlier bench's ruler) | 32 MiB | **238.6** | [239.5, 238.6, 229.3] |
| a2_torch_sum_big | torch .sum(), one 2.0 GiB bf16 buffer | 2048 MiB | **240.8** | [240.4, 241.2, 240.8] |
| b_memcpy_d2d | cudaMemcpyDeviceToDevice, 2.0 GiB buffer | 2048 MiB | **245.9** | [244.6, 246.1, 245.9] |
| c_custom_stream_read | custom __ldg/uint4 streaming read, 2.0 GiB | 2048 MiB | **239.5** | [238.9, 239.7, 239.5] |
| spec | LPDDR5X datasheet peak | - | 273.0 | - |

**Ceiling used below: 245.9 GB/s** — the best of the measured rulers, and 90 % of the datasheet peak.

The three methods agree inside 3 %. The raw `cudaMemcpyD2D` copy rate is 123.0 GB/s; the
read-equivalent figure that counts both the read and the write traffic is 245.9. A hand-written
`__ldg` / `uint4` reader lands at 239.5 — it does **not** beat `torch.sum()`, so the ceiling is a
property of the part and not of the instrument. The earlier ruler was sound.

## R2. Measured resident weight bytes per expert

Byte counts taken from the arms' actually-resident tensors, not from a formula. `[measured-here]`

| arm | bytes/expert measured | vs 4-bit + fp8-scale model |
|---|---|---|
| marlin W4A16 | 14,155,784 | 1.0000x |
| cutlass W4A4 | 14,155,784 | 1.0000x |
| b12x W4A4 | 14,155,776 | 1.0000x |
| bf16 | 50,331,648 | 3.5556x |

All three quantised arms move the **same** bytes per expert; the 8-byte difference is a swizzle and
alignment remainder, not a marlin workspace. Every timing difference between them is therefore
**efficiency, not byte count**.

## Crossover — the per-M tables

`x mar` is the arm's time divided by marlin's at the same point; below 1.00 is faster than marlin.
`best-FP4` is whichever of cutlass and b12x was faster there. `[measured-here]`

### C1. Layout A — form `dense` (all-8-local), routing `uniform`

| M | rows | experts | marlin us | cutlass us | cutlass x mar | b12x us | b12x x mar | bf16 us | marlin GB/s | best-FP4 GB/s | marlin TF | best-FP4 TF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 8 | 47 | 2834 | 3114 | 1.10 | 2963 | 1.05 | 10176 | 234.8 | 224.6 | 1.14 | 1.09 |
| 16 | 16 | 71 | 4265 | 4636 | 1.09 | 4514 | 1.06 | 15281 | 235.6 | 222.7 | 1.51 | 1.43 |
| 32 | 32 | 90 | 5406 | 5895 | 1.09 | 5756 | 1.06 | 19448 | 235.7 | 221.3 | 2.38 | 2.24 |
| 64 | 64 | 96 | 5835 | 6382 | 1.09 | 6193 | 1.06 | 21203 | 232.9 | 219.5 | 4.42 | 4.16 |
| 128 | 128 | 96 | 5949 | 6576 | 1.11 | 6283 | 1.06 | 24088 | 228.4 | 216.3 | 8.66 | 8.2 |
| 256 | 256 | 96 | 5976 | 6954 | 1.16 | 6333 | 1.06 | 24110 | 227.4 | 214.6 | 17.25 | 16.28 |
| 512 | 512 | 96 | 6619 | 7985 | 1.21 | 7032 | 1.06 | 24355 | 205.3 | 193.3 | 31.15 | 29.32 |
| 1024 | 1024 | 96 | 10355 | 9716 | 0.94 | 7585 | 0.73 | 31038 | 131.2 | 179.2 | 39.82 | 54.36 |
| 1792 | 1792 | 96 | 13144 | 12454 | 0.95 | 9505 | 0.72 | 48352 | 103.4 | 143.0 | 54.9 | 75.92 |
| 4096 | 4096 | 96 | 25806 | 20566 | 0.80 | 13986 | 0.54 | - | 52.7 | 97.2 | 63.91 | 117.92 |

**Crossover M (best FP4 first beats marlin by > 5 %): 1024**

### C2. Layout A — form `dense` (all-8-local), routing `zipf`

| M | rows | experts | marlin us | cutlass us | cutlass x mar | b12x us | b12x x mar | bf16 us | marlin GB/s | best-FP4 GB/s | marlin TF | best-FP4 TF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 8 | 36 | 2164 | 2406 | 1.11 | 2266 | 1.05 | 7794 | 235.5 | 224.9 | 1.49 | 1.42 |
| 16 | 16 | 52 | 3154 | 3433 | 1.09 | 3311 | 1.05 | 11364 | 233.4 | 222.3 | 2.04 | 1.95 |
| 32 | 32 | 66 | 4044 | 4382 | 1.08 | 4218 | 1.04 | 14622 | 231.1 | 221.5 | 3.19 | 3.06 |
| 64 | 64 | 82 | 5079 | 5479 | 1.08 | 5274 | 1.04 | 18624 | 228.5 | 220.1 | 5.07 | 4.89 |
| 128 | 128 | 95 | 5869 | 6487 | 1.11 | 6286 | 1.07 | 23902 | 229.1 | 213.9 | 8.78 | 8.2 |
| 256 | 256 | 96 | 6214 | 6915 | 1.11 | 6648 | 1.07 | 24776 | 218.7 | 204.4 | 16.59 | 15.51 |
| 512 | 512 | 96 | 7034 | 7921 | 1.13 | 7542 | 1.07 | 27157 | 193.2 | 180.2 | 29.31 | 27.34 |
| 1024 | 1024 | 96 | 9432 | 9722 | 1.03 | 8781 | 0.93 | 35566 | 144.1 | 154.8 | 43.72 | 46.96 |
| 1792 | 1792 | 96 | 13315 | 12704 | 0.95 | 10083 | 0.76 | 43841 | 102.1 | 134.8 | 54.19 | 71.56 |
| 4096 | 4096 | 96 | 25972 | 21508 | 0.83 | 16092 | 0.62 | - | 52.3 | 84.4 | 63.5 | 102.49 |

**Crossover M (best FP4 first beats marlin by > 5 %): 1024**

### C3. Layout A — form `epcompact` (EP-effective, the production form), routing `uniform`

| M | rows | experts | marlin us | cutlass us | cutlass x mar | b12x us | b12x x mar | bf16 us | marlin GB/s | best-FP4 GB/s | marlin TF | best-FP4 TF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 24 | 20 | 1273 | 1381 | 1.08 | 1290 | 1.01 | 5387 | 222.4 | 219.5 | 0.95 | 0.94 |
| 16 | 41 | 34 | 2053 | 2272 | 1.11 | 2169 | 1.06 | 7456 | 234.4 | 221.9 | 1.01 | 0.95 |
| 32 | 86 | 56 | 3385 | 3701 | 1.09 | 3556 | 1.05 | 14215 | 234.2 | 222.9 | 1.28 | 1.22 |
| 64 | 192 | 86 | 5190 | 5662 | 1.09 | 5512 | 1.06 | 21225 | 234.5 | 220.9 | 1.86 | 1.75 |
| 128 | 345 | 91 | 5536 | 6054 | 1.09 | 5870 | 1.06 | 22413 | 232.7 | 219.4 | 3.14 | 2.96 |
| 256 | 662 | 95 | 6001 | 6468 | 1.08 | 6062 | 1.01 | 25969 | 224.1 | 221.8 | 5.55 | 5.5 |
| 512 | 1350 | 96 | 6242 | 6845 | 1.10 | 6639 | 1.06 | 26810 | 217.7 | 204.7 | 10.89 | 10.23 |
| 1024 | 2720 | 96 | 6438 | 7701 | 1.20 | 7094 | 1.10 | 28083 | 211.1 | 191.6 | 21.26 | 19.3 |
| 1792 | 4854 | 96 | 6909 | 8889 | 1.29 | 7193 | 1.04 | 29605 | 196.7 | 188.9 | 35.36 | 33.96 |
| 4096 | 10929 | 96 | 10338 | 12092 | 1.17 | 8874 | 0.86 | - | 131.4 | 153.1 | 53.21 | 61.99 |

**Crossover M (best FP4 first beats marlin by > 5 %): 4096**

### C4. Layout A — form `epcompact` (EP-effective), routing `zipf`

Read this table as **worst-case rank imbalance**, not as a hot-expert reading: expert parallelism
gives rank 0 exactly the ids the Zipf mass sits on, so at M = 1,792 this rank holds 11,457 of 14,336
pairs — 80 % against 33 % in balance.

| M | rows | experts | marlin us | cutlass us | cutlass x mar | b12x us | b12x x mar | bf16 us | marlin GB/s | best-FP4 GB/s | marlin TF | best-FP4 TF |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 8 | 54 | 28 | 1724 | 1898 | 1.10 | 1794 | 1.04 | 6257 | 230.0 | 221.0 | 1.58 | 1.52 |
| 16 | 107 | 45 | 2730 | 3011 | 1.10 | 2908 | 1.07 | 11478 | 233.3 | 219.0 | 1.97 | 1.85 |
| 32 | 211 | 68 | 4151 | 4526 | 1.09 | 4381 | 1.06 | 17265 | 231.9 | 219.7 | 2.56 | 2.42 |
| 64 | 410 | 85 | 5186 | 5698 | 1.10 | 5490 | 1.06 | 21029 | 232.0 | 219.2 | 3.98 | 3.76 |
| 128 | 797 | 93 | 5783 | 6399 | 1.11 | 6192 | 1.07 | 25484 | 227.6 | 212.6 | 6.94 | 6.48 |
| 256 | 1620 | 96 | 6125 | 6931 | 1.13 | 6616 | 1.08 | 27170 | 221.9 | 205.4 | 13.31 | 12.32 |
| 512 | 3229 | 96 | 6825 | 7924 | 1.16 | 7510 | 1.10 | 29312 | 199.1 | 180.9 | 23.81 | 21.64 |
| 1024 | 6600 | 96 | 8591 | 9773 | 1.14 | 8504 | 0.99 | 33460 | 158.2 | 159.8 | 38.67 | 39.06 |
| 1792 | 11457 | 96 | 11823 | 12478 | 1.06 | 10053 | 0.85 | 40032 | 114.9 | 135.2 | 48.77 | 57.36 |
| 4096 | 26211 | 96 | 22243 | 20635 | 0.93 | 15790 | 0.71 | - | 61.1 | 86.1 | 59.31 | 83.55 |

**Crossover M (best FP4 first beats marlin by > 5 %): 1792**

### C5. Crossover summary

| form | routing | crossover M | best FP4 there |
|---|---|---|---|
| all-8-local (`dense`) | uniform | **1024** | b12x 0.73x marlin |
| all-8-local (`dense`) | zipf | **1024** | b12x 0.93x marlin |
| EP-effective (`epcompact`) | uniform | **4096** | b12x 0.86x marlin |
| EP-effective (`epcompact`) | zipf | **1792** | b12x 0.85x marlin |

**In the production form, marlin is ahead across the whole M = 8 … 1,792 range.** On the balanced
reading FP4 does not get in front until M = 4,096, and with `max-num-batched-tokens` at 2,048 a
single forward pass never reaches that point.

## G1. The GEMM-only bound — what a perfectly fused custom FP4 kernel cannot go below

`gemm-only` = the two grouped FP4 tensor-core GEMMs with pre-quantised activations, pre-built routing
metadata, no row shuffle and no epilogue combine. `[measured-here]`

| form | M | routing | marlin us | cutlass full us | GEMM-only us | act-quant us | meta+combine us | GEMM-only / marlin | GEMM-only GB/s | GEMM-only TF |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 8 | uniform | 2834 | 3107 | **3040** | 148 | 242 | **1.07x** | 218.9 | 1.06 |
| dense | 8 | zipf | 2164 | 2408 | **2339** | 148 | 243 | **1.08x** | 217.9 | 1.38 |
| dense | 16 | uniform | 4265 | 4627 | **4535** | 148 | 242 | **1.06x** | 221.6 | 1.42 |
| dense | 16 | zipf | 3154 | 3447 | **3358** | 148 | 241 | **1.06x** | 219.2 | 1.92 |
| dense | 32 | uniform | 5406 | 5896 | **5768** | 148 | 241 | **1.07x** | 220.9 | 2.23 |
| dense | 32 | zipf | 4044 | 4392 | **4259** | 146 | 243 | **1.05x** | 219.4 | 3.03 |
| dense | 64 | uniform | 5835 | 6394 | **6196** | 147 | 243 | **1.06x** | 219.3 | 4.16 |
| dense | 64 | zipf | 5079 | 5490 | **5293** | 148 | 246 | **1.04x** | 219.3 | 4.87 |
| dense | 128 | uniform | 5949 | 6585 | **6315** | 147 | 247 | **1.06x** | 215.2 | 8.16 |
| dense | 128 | zipf | 5869 | 6494 | **6194** | 147 | 246 | **1.06x** | 217.1 | 8.32 |
| dense | 256 | uniform | 5976 | 6969 | **6421** | 228 | 312 | **1.07x** | 211.6 | 16.05 |
| dense | 256 | zipf | 6214 | 6944 | **6403** | 218 | 319 | **1.03x** | 212.3 | 16.1 |
| dense | 512 | uniform | 6619 | 8001 | **6660** | 563 | 771 | **1.01x** | 204.1 | 30.96 |
| dense | 512 | zipf | 7034 | 7949 | **6615** | 570 | 778 | **0.94x** | 205.4 | 31.17 |
| dense | 1024 | uniform | 10355 | 9788 | **7099** | 1147 | 1532 | **0.69x** | 191.4 | 58.08 |
| dense | 1024 | zipf | 9432 | 9768 | **7103** | 1137 | 1559 | **0.75x** | 191.3 | 58.05 |
| dense | 1792 | uniform | 13144 | 12461 | **7422** | 2052 | 2673 | **0.56x** | 183.1 | 97.22 |
| dense | 1792 | zipf | 13315 | 12770 | **7826** | 2099 | 2649 | **0.59x** | 173.7 | 92.2 |
| epcompact | 8 | uniform | 1273 | 1381 | **1328** | 144 | 237 | **1.04x** | 213.2 | 0.91 |
| epcompact | 8 | zipf | 1724 | 1915 | **1844** | 145 | 238 | **1.07x** | 214.9 | 1.47 |
| epcompact | 16 | uniform | 2053 | 2306 | **2254** | 145 | 236 | **1.10x** | 213.5 | 0.92 |
| epcompact | 16 | zipf | 2730 | 3024 | **2915** | 144 | 238 | **1.07x** | 218.5 | 1.85 |
| epcompact | 32 | uniform | 3385 | 3716 | **3622** | 145 | 237 | **1.07x** | 218.9 | 1.2 |
| epcompact | 32 | zipf | 4151 | 4535 | **4397** | 144 | 237 | **1.06x** | 218.9 | 2.42 |
| epcompact | 64 | uniform | 5190 | 5678 | **5529** | 142 | 236 | **1.07x** | 220.2 | 1.75 |
| epcompact | 64 | zipf | 5186 | 5695 | **5504** | 146 | 237 | **1.06x** | 218.6 | 3.75 |
| epcompact | 128 | uniform | 5536 | 6089 | **5890** | 145 | 237 | **1.06x** | 218.7 | 2.95 |
| epcompact | 128 | zipf | 5783 | 6394 | **6061** | 145 | 243 | **1.05x** | 217.2 | 6.62 |
| epcompact | 256 | uniform | 6001 | 6463 | **6191** | 146 | 242 | **1.03x** | 217.2 | 5.38 |
| epcompact | 256 | zipf | 6125 | 6977 | **6363** | 224 | 371 | **1.04x** | 213.6 | 12.81 |
| epcompact | 512 | uniform | 6242 | 6861 | **6354** | 195 | 302 | **1.02x** | 213.9 | 10.69 |
| epcompact | 512 | zipf | 6825 | 7971 | **6562** | 527 | 918 | **0.96x** | 207.1 | 24.77 |
| epcompact | 1024 | uniform | 6438 | 7697 | **6560** | 449 | 741 | **1.02x** | 207.2 | 20.87 |
| epcompact | 1024 | zipf | 8591 | 9812 | **6929** | 1093 | 1836 | **0.81x** | 196.1 | 47.94 |
| epcompact | 1792 | uniform | 6909 | 8881 | **6765** | 798 | 1361 | **0.98x** | 200.9 | 36.11 |
| epcompact | 1792 | zipf | 11823 | 12537 | **7618** | 1874 | 3166 | **0.64x** | 178.4 | 75.69 |

**The one sentence for this table.** At M ≤ 512, deleting the activation quantisation, the row
shuffle, the metadata and the epilogue combine **entirely** still leaves the FP4 GEMM slower than
marlin's complete path (1.02–1.10×). In that regime act-quant is only **142–148 us** of the call,
about 3 %, so the "fuse it and we win" argument has no material behind it. The break starts at
M = 1,024, where GEMM-only is 0.69–0.75×, and reaches **0.56×** at M = 1,792 all-8-local.

## RF1. Roofline and percentage of roofline

`[measured-here]` for the timed columns; `t_mem`, `t_fp4`, `t_bf16` and the roofline columns are
derived from the measured bytes and the 245.9 GB/s ceiling by the model above.

| form | M | routing | weight MiB | t_mem us | t_fp4 us | t_bf16 us | roofline_fp4 us | roofline_bf16 us | marlin us | marlin % of roofline | best FP4 us | best FP4 % of roofline | honest ceiling (marlin/roofline_fp4) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dense | 8 | uniform | 635 | 2706 | 6 | 33 | 2706 | 2706 | 2834 | 95 % | 2963 | 91 % | **1.05x** |
| dense | 8 | zipf | 486 | 2073 | 6 | 33 | 2073 | 2073 | 2164 | 96 % | 2266 | 91 % | **1.04x** |
| dense | 16 | uniform | 959 | 4088 | 13 | 66 | 4088 | 4088 | 4265 | 96 % | 4514 | 91 % | **1.04x** |
| dense | 16 | zipf | 702 | 2995 | 13 | 66 | 2995 | 2995 | 3154 | 95 % | 3311 | 90 % | **1.05x** |
| dense | 32 | uniform | 1215 | 5183 | 26 | 132 | 5183 | 5183 | 5406 | 96 % | 5756 | 90 % | **1.04x** |
| dense | 32 | zipf | 891 | 3802 | 26 | 132 | 3802 | 3802 | 4044 | 94 % | 4218 | 90 % | **1.06x** |
| dense | 64 | uniform | 1296 | 5531 | 52 | 265 | 5531 | 5531 | 5835 | 95 % | 6193 | 89 % | **1.05x** |
| dense | 64 | zipf | 1107 | 4725 | 52 | 265 | 4725 | 4725 | 5079 | 93 % | 5274 | 90 % | **1.07x** |
| dense | 128 | uniform | 1296 | 5535 | 103 | 530 | 5535 | 5535 | 5949 | 93 % | 6283 | 88 % | **1.07x** |
| dense | 128 | zipf | 1283 | 5477 | 103 | 530 | 5477 | 5477 | 5869 | 93 % | 6286 | 87 % | **1.07x** |
| dense | 256 | uniform | 1296 | 5544 | 206 | 1059 | 5544 | 5544 | 5976 | 93 % | 6333 | 88 % | **1.08x** |
| dense | 256 | zipf | 1296 | 5544 | 206 | 1059 | 5544 | 5544 | 6214 | 89 % | 6648 | 83 % | **1.12x** |
| dense | 512 | uniform | 1296 | 5561 | 412 | 2119 | 5561 | 5561 | 6619 | 84 % | 7032 | 79 % | **1.19x** |
| dense | 512 | zipf | 1296 | 5561 | 412 | 2119 | 5561 | 5561 | 7034 | 79 % | 7542 | 74 % | **1.26x** |
| dense | 1024 | uniform | 1296 | 5595 | 825 | 4238 | 5595 | 5595 | 10355 | 54 % | 7585 | 74 % | **1.85x** |
| dense | 1024 | zipf | 1296 | 5595 | 825 | 4238 | 5595 | 5595 | 9432 | 59 % | 8781 | 64 % | **1.69x** |
| dense | 1792 | uniform | 1296 | 5646 | 1443 | 7416 | 5646 | 7416 | 13144 | 56 % | 9505 | 59 % | **2.33x** |
| dense | 1792 | zipf | 1296 | 5646 | 1443 | 7416 | 5646 | 7416 | 13315 | 56 % | 10083 | 56 % | **2.36x** |
| dense | 4096 | uniform | 1296 | 5799 | 3299 | 16950 | 5799 | 16950 | 25806 | 66 % | 13986 | 41 % | **4.45x** |
| dense | 4096 | zipf | 1296 | 5799 | 3299 | 16950 | 5799 | 16950 | 25972 | 65 % | 16092 | 36 % | **4.48x** |
| epcompact | 8 | uniform | 270 | 1153 | 2 | 12 | 1153 | 1153 | 1273 | 91 % | 1290 | 89 % | **1.10x** |
| epcompact | 8 | zipf | 378 | 1615 | 5 | 28 | 1615 | 1615 | 1724 | 94 % | 1794 | 90 % | **1.07x** |
| epcompact | 16 | uniform | 459 | 1960 | 4 | 21 | 1960 | 1960 | 2053 | 95 % | 2169 | 90 % | **1.05x** |
| epcompact | 16 | zipf | 608 | 2598 | 11 | 55 | 2598 | 2598 | 2730 | 95 % | 2908 | 89 % | **1.05x** |
| epcompact | 32 | uniform | 756 | 3229 | 9 | 45 | 3229 | 3229 | 3385 | 95 % | 3556 | 91 % | **1.05x** |
| epcompact | 32 | zipf | 918 | 3929 | 21 | 109 | 3929 | 3929 | 4151 | 95 % | 4381 | 90 % | **1.06x** |
| epcompact | 64 | uniform | 1161 | 4964 | 19 | 99 | 4964 | 4964 | 5190 | 96 % | 5512 | 90 % | **1.05x** |
| epcompact | 64 | zipf | 1148 | 4921 | 41 | 212 | 4921 | 4921 | 5186 | 95 % | 5490 | 90 % | **1.05x** |
| epcompact | 128 | uniform | 1229 | 5262 | 35 | 178 | 5262 | 5262 | 5536 | 95 % | 5870 | 90 % | **1.05x** |
| epcompact | 128 | zipf | 1256 | 5407 | 80 | 412 | 5407 | 5407 | 5783 | 93 % | 6192 | 87 % | **1.07x** |
| epcompact | 256 | uniform | 1283 | 5513 | 67 | 342 | 5513 | 5513 | 6001 | 92 % | 6062 | 91 % | **1.09x** |
| epcompact | 256 | zipf | 1296 | 5634 | 163 | 838 | 5634 | 5634 | 6125 | 92 % | 6616 | 85 % | **1.09x** |
| epcompact | 512 | uniform | 1296 | 5616 | 136 | 698 | 5616 | 5616 | 6242 | 90 % | 6639 | 85 % | **1.11x** |
| epcompact | 512 | zipf | 1296 | 5742 | 325 | 1670 | 5742 | 5742 | 6825 | 84 % | 7510 | 76 % | **1.19x** |
| epcompact | 1024 | uniform | 1296 | 5708 | 274 | 1407 | 5708 | 5708 | 6438 | 89 % | 7094 | 80 % | **1.13x** |
| epcompact | 1024 | zipf | 1296 | 5966 | 664 | 3414 | 5966 | 5966 | 8591 | 69 % | 8504 | 70 % | **1.44x** |
| epcompact | 1792 | uniform | 1296 | 5850 | 489 | 2511 | 5850 | 5850 | 6909 | 85 % | 7193 | 81 % | **1.18x** |
| epcompact | 1792 | zipf | 1296 | 6290 | 1153 | 5927 | 6290 | 6290 | 11823 | 53 % | 10053 | 63 % | **1.88x** |
| epcompact | 4096 | uniform | 1296 | 6255 | 1100 | 5653 | 6255 | 6255 | 10338 | 60 % | 8874 | 70 % | **1.65x** |
| epcompact | 4096 | zipf | 1296 | 7273 | 2638 | 13558 | 7273 | 13558 | 22243 | 61 % | 15790 | 46 % | **3.06x** |

Note `t_fp4` against `t_mem`. Even at a 500 TFLOPS FP4 peak, arithmetic never becomes the binding
constraint below M ≈ 4,096 in either form. At M = 64 the MoE stage needs 52 us of FP4 math and
5,531 us of DRAM time.

## V1. The pre-registered verdict, at every operating point

Criterion A: GEMM-only FP4 beats marlin by > 10 % (`gemm-only/marlin < 0.90`).
Criterion B: `roofline_fp4` more than 1.25× faster than measured marlin
(`marlin/roofline_fp4 > 1.25`).
SUPPORTED if A or B holds, otherwise NOT SUPPORTED. Both criteria and both thresholds were written
before the run. `[measured-here]`

| operating point | form | routing | marlin us | GEMM-only us | A: gemm/marlin | B: marlin/roofline_fp4 | max gain of a custom path | VERDICT |
|---|---|---|---|---|---|---|---|---|
| M=8 (1 stream, DFlash2 k=7) | dense | uniform | 2834 | 3040 | 1.07 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=8 (1 stream, DFlash2 k=7) | epcompact | uniform | 1273 | 1328 | 1.04 no | 1.10 no | 9 % | NOT SUPPORTED |
| M=64 (8 streams) | dense | uniform | 5835 | 6196 | 1.06 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=64 (8 streams) | epcompact | uniform | 5190 | 5529 | 1.07 no | 1.05 no | 4 % | NOT SUPPORTED |
| M=128 (16 streams) | dense | uniform | 5949 | 6315 | 1.06 no | 1.07 no | 7 % | NOT SUPPORTED |
| M=128 (16 streams) | epcompact | uniform | 5536 | 5890 | 1.06 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=256 (32 streams) | dense | uniform | 5976 | 6421 | 1.07 no | 1.08 no | 7 % | NOT SUPPORTED |
| M=256 (32 streams) | epcompact | uniform | 6001 | 6191 | 1.03 no | 1.09 no | 8 % | NOT SUPPORTED |
| M=1792 (prefill chunk) | dense | uniform | 13144 | 7422 | 0.56 YES | 2.33 YES | 57 % | **SUPPORTED** |
| M=1792 (prefill chunk) | epcompact | uniform | 6909 | 6765 | 0.98 no | 1.18 no | 15 % | NOT SUPPORTED |

Under `zipf` — the worst rank imbalance — the only point that moves is M = 1,792 `epcompact`:
GEMM-only 7,618 against marlin 11,823 = **0.64×**, roofline ratio **1.88×**, gain ceiling 47 %, so
the theory is **SUPPORTED** there. Which gives the verdict its final shape: **the theory holds in
the non-EP form and under a pathology in which one rank swallows the hot experts; under balanced
production traffic it does not.**

## The two flaws, and what fixing them changed

### Flaw 1 — coverage: the earlier M grid stepped over a marlin cliff

The earlier bench measured M ∈ {8, 64, 1792} and reported b12x 1.42× ahead at 1,792 and 6 % behind
at 64. The turn was somewhere between them and nothing was measured there, while the objection —
*better under concurrency* — is about exactly that gap: C16 and C32 sit at M = 128 and 256.

Filling it found something neither side expected. In the all-8-local form **marlin has a cliff
between M = 512 and M = 1,024**: 6,619 → 10,355 us, +56 %, achieved bandwidth 205 → 131 GB/s. It is
not noise — round-to-round spread at those two points is 0.21 % and 1.89 %. The FP4 grouped GEMM
does not have it (6,660 → 7,099 us). `[measured-here]`

That cliff **is** the all-8-local FP4 advantage. It is a marlin scheduling artefact at large row
counts per expert, not an FP4 tensor-core win, and it puts the all-8-local crossover at M = 1,024
rather than the M = 1,792 the earlier grid implied. It does not appear in the EP-effective form,
where rows are spread thinner across the 96 local experts.

### Flaw 2 — the FP4 path's plumbing was charged to the FP4 kernel

`run_cutlass_moe_fp4` allocates six metadata tensors, builds routing data, shuffles rows twice,
quantises activations twice and runs a combine — all of which the earlier bench timed inside
"cutlass W4A4" and compared against marlin's complete path. At prefill size that is a large charge:
at M = 1,792 all-8-local, **2,052 us of act-quant and 2,673 us of metadata and combine out of
12,461 us — 38 % of the call is not the GEMM.** `[measured-here]`

Stripped to the GEMM, the picture at prefill changes and the earlier bench's *"cutlass is 29 % slower
than marlin"* line does not survive as a kernel statement: in the production EP form GEMM-only is
**0.98×**, level with marlin, and in the all-8-local form it is **0.56×**, clearly ahead. That is a
correction owed to the author and it is recorded as one.

What it does not do is rescue the decode case, because at M ≤ 512 the same plumbing costs only
142–148 us — about 3 % — and removing all of it still leaves the FP4 GEMM at 1.02–1.10× marlin.

## Two hypotheses raised and disproved

Both were raised against the earlier bench before this run, and both failed. They are recorded
because each one, if true, would have invalidated a table.

**1. "Marlin inflates its scales to bf16, so its byte accounting is wrong."** `marlin_utils_fp4.py`
does contain `scales = scales.to(param_dtype)`. If those scales stayed bf16, marlin would be reading
about 11 % more bytes than the FP4 arms and every GB/s figure on the earlier page would be wrong.
Reading `nvfp4_marlin_process_scales` shows the conversion is a way-station — the comment says the
half conversion happens first and fp8 later — and the scales end as 1-byte S0E5M3 fp8. This sweep
did not take that on trust: the byte count came from the arms' resident tensors, and all three
quantised arms carry the same 14,155,78x B per expert (table R2). **The earlier byte accounting was
correct.** `[measured-here]`

**2. "The earlier bench bypassed b12x's own profile layer, so it ran the wrong backend."** It called
`flashinfer.fused_moe.B12xMoEWrapper` directly — and so does production
(`flashinfer_b12x_moe.py` constructs the same wrapper). **No production layer was skipped.**
`[measured-here]`

One qualification does come out of that check, and it weakens a sentence on the earlier page. That
page reported that b12x's own measured `nvidia.gb10.48sm` profile selects `"backend": "w4a16"` for
its `moe.ep_moe` component. True, and still a meaningful vendor signal — but that profile belongs to
the `b12x` package's own policy layer (`b12x/policy`, `b12x/integration/tp_moe.py`), which vLLM's
`flashinfer_b12x` backend does not go through; our path selects by rows routed per call
(`select_sm120_moe_backend()` with `_STATIC_COMPACT_CUTOVER_PAIRS = 640`). It should be read as a
signal about what the vendor measured, not as a statement about the code we run.

## Two concrete, checkable things for a custom sm_121 path

**1. Weight-streaming efficiency at memory-bound sizes — the first thing to close.** On identical
byte counts the FP4 grouped GEMM sustains **211–220 GB/s** where marlin's complete W4A16 path
sustains **228–236 GB/s**: 89 % against 95 % of the 245.9 GB/s ceiling. At our operating points that
roughly 7 % gap is worth more than any tensor-core work, and it is measurable without a model.
`[measured-here]`

**2. Wrapper sizing, worth 8.8 % and 9.6 % — and free.** Building the SM12x MoE wrapper with
`max_num_tokens` matched to the actual batch rather than to a fixed upper bound is worth **8.8 % at
M = 128 and 9.6 % at M = 256** in the all-8-local form. In 36 of 40 sweep points the difference is
≤ 2 %, and there is no measurable effect in the EP form, so this is a real but narrow win — it
changes no verdict, since b12x at matched size is still 6 % behind marlin at M = 128. Integrations
that construct the wrapper once from `max_num_batched_tokens` — which is what production does here,
and why the earlier bench's fixed-2048 wrapper was faithful rather than unfair — leave it on the
table. `[measured-here]`

### A1. b12x wrapper sizing A/B (`max_num_tokens` matched to M against always ≥ 2,048)

| form | M | routing | matched us | fixed>=2048 us | fixed/matched |
|---|---|---|---|---|---|
| dense | 8 | uniform | 2963 | 3011 | 1.016 |
| dense | 8 | zipf | 2266 | 2291 | 1.011 |
| dense | 16 | uniform | 4514 | 4523 | 1.002 |
| dense | 16 | zipf | 3311 | 3300 | 0.997 |
| dense | 32 | uniform | 5756 | 5755 | 1.000 |
| dense | 32 | zipf | 4218 | 4215 | 0.999 |
| dense | 64 | uniform | 6193 | 6160 | 0.995 |
| dense | 64 | zipf | 5274 | 5264 | 0.998 |
| dense | 128 | uniform | 6283 | 6839 | 1.088 |
| dense | 128 | zipf | 6286 | 6728 | 1.070 |
| dense | 256 | uniform | 6333 | 6943 | 1.096 |
| dense | 256 | zipf | 6648 | 6989 | 1.051 |
| dense | 512 | uniform | 7032 | 7146 | 1.016 |
| dense | 512 | zipf | 7542 | 7379 | 0.978 |
| dense | 1024 | uniform | 7585 | 7656 | 1.009 |
| dense | 1024 | zipf | 8781 | 8471 | 0.965 |
| dense | 1792 | uniform | 9505 | 9449 | 0.994 |
| dense | 1792 | zipf | 10083 | 10130 | 1.005 |
| dense | 4096 | uniform | 13986 | 13961 | 0.998 |
| dense | 4096 | zipf | 16092 | 16109 | 1.001 |
| epcompact | 8 | uniform | 1290 | 1308 | 1.014 |
| epcompact | 8 | zipf | 1794 | 1808 | 1.008 |
| epcompact | 16 | uniform | 2169 | 2213 | 1.020 |
| epcompact | 16 | zipf | 2908 | 2912 | 1.001 |
| epcompact | 32 | uniform | 3556 | 3611 | 1.015 |
| epcompact | 32 | zipf | 4381 | 4370 | 0.997 |
| epcompact | 64 | uniform | 5512 | 5502 | 0.998 |
| epcompact | 64 | zipf | 5490 | 5514 | 1.004 |
| epcompact | 128 | uniform | 5870 | 5859 | 0.998 |
| epcompact | 128 | zipf | 6192 | 6112 | 0.987 |
| epcompact | 256 | uniform | 6062 | 6111 | 1.008 |
| epcompact | 256 | zipf | 6616 | 6625 | 1.001 |
| epcompact | 512 | uniform | 6639 | 6334 | 0.954 |
| epcompact | 512 | zipf | 7510 | 7450 | 0.992 |
| epcompact | 1024 | uniform | 7094 | 7170 | 1.011 |
| epcompact | 1024 | zipf | 8504 | 8556 | 1.006 |
| epcompact | 1792 | uniform | 7193 | 7212 | 1.003 |
| epcompact | 1792 | zipf | 10053 | 10035 | 0.998 |
| epcompact | 4096 | uniform | 8874 | 8890 | 1.002 |
| epcompact | 4096 | zipf | 15790 | 15873 | 1.005 |

## What this sweep does not say

- **What share of a real step the MoE occupies.** Every ratio here is inside one MoE kernel call. If
  the MoE is 10 % of a step, a 57 % kernel win at all-local prefill is under 6 % of that step. That
  is a profiler measurement and it was not run here. `[not tested]`
- **Communication.** Single GPU. The all-to-all around an expert-parallel rank is invisible to this
  bench. `[not tested]`
- **Quality.** No correctness gate was run in this sweep — the arms were gated by the earlier bench
  at these shapes, and what W4A4 would do to MMLU or the code exam on the real model is still
  unmeasured. `[not tested]`
- **Real routing.** `uniform` and `zipf(s=1)` over expert ids; the real router's token-expert
  distribution was not measured. The marlin ↔ FP4 ratio is close under both, which is evidence that
  the verdict is insensitive to the distribution, not proof.
- **Synthetic weights.** Byte count and shape are the real ones, so the speed figures stand; nothing
  here binds on the real checkpoint's numerics.
- **Eager only.** No CUDA graphs, so every arm pays the same launch cost and the ratios are
  comparable; the absolute microseconds include per-call launch overhead.
- **Three rounds is three rounds.** Median spread 0.52 %, max 4.17 %, all rounds in every JSON. That
  is why the crossover threshold is 5 % and not 1 %.
- **M = 4,096 for `split` and `bf16` is missing**, see below. It is above the production
  `max-num-batched-tokens` and no verdict depends on it.

## What it cost

Fifteen minutes of one GPU. Peak GPU allocation 6.94 GiB (the marlin repack). `[measured-here]`

| stage | build s | GPU peak GiB | host MemAvailable min GiB | weight sets |
|---|---|---|---|---|
| ruler | - | - | 11.35 | - |
| marlin | 4.9 | 6.94 | 7.91 | 2 |
| cutlass | 0.7 | 4.03 | 7.77 | 2 |
| b12x matched | 4.0 | 3.28 | 7.48 | 2 |
| b12x fixed>=2048 | 3.9 | 3.28 | 7.53 | 2 |
| cutlass split | 0.7 | 4.01 | 8.0 | 2 |
| bf16 | 3.1 | 5.78 | 6.26 | 1 |

- **The engine was untouched and stayed healthy.** It was up and idle throughout
  (`running/waiting = 0`), was sent one warm-up request at the end (200, "OK"), and nothing went into
  swap. The earlier bench's cost — `MemAvailable` down to 1.0 GiB and a resident engine 3.4 GiB into
  swap — was **not** repeated: building and prepping one weight set at a time brought the GPU peak
  from 8.2 GiB to about 3.5 GiB, and `MemAvailable`, 12.34 GiB at the start, never went below
  6.26 GiB in an accepted arm.
- **The wait.** The cluster measurement lock was held by other work from 02:29, so this sweep waited
  **2 hours 2 minutes** for it, took it at 05:10:55 and released it at 05:27.
- **What was lost.** The `split` and `bf16` arms were killed at M = 4,096 by the run's own
  `MemAvailable` watchdog at 5.9 GiB (`rc=137` in [`RUN.log`](gb10-crossover/RUN.log)), exactly as the
  rule said it should. Both were re-run capped at M ≤ 1,792. M = 4,096 is above the production
  `max-num-batched-tokens` of 2,048, so this affects no verdict — but it is the reason the GEMM-only
  and bf16 tables stop at 1,792 while the four sweep tables go to 4,096.
- **No throttling.** 0 of 153 samples.

---

# Part 3 — For the kernel author

*This section is reproduced from the source report unchanged, apart from its section numbering and
one cross-reference, which now points at the tables on this page. It is written to stand alone, so
it repeats numbers given above.*

*(Single GB10 (sm_121), 48 SMs, CUDA 13.0, torch 2.13. No engine in the measurement; synthetic NVFP4
expert banks in checkpoint layout, fed to the same kernels a production vLLM build would call. CUDA
events, 20 warm-up, 50 timed iterations x 3 rounds, round median. Weights rotated over 2 distinct
banks; worst-case weight traffic per call 0.26 GiB = 11x the 24 MiB L2, typically 0.6-1.3 GiB. Zero
throttle events in 153 samples.)*

## Ruler — the memory ceiling, three independent ways

| ruler | method | buffer | GB/s | rounds |
|---|---|---|---|---|
| a_torch_sum_small | torch .sum(), 4 x 32 MiB bf16 (tonight's ruler) | 32 MiB | **238.6** | [239.5, 238.6, 229.3] |
| a2_torch_sum_big | torch .sum(), one 2.0 GiB bf16 buffer | 2048 MiB | **240.8** | [240.4, 241.2, 240.8] |
| b_memcpy_d2d | cudaMemcpyDeviceToDevice, 2.0 GiB buffer | 2048 MiB | **245.9** | [244.6, 246.1, 245.9] |
| c_custom_stream_read | custom __ldg/uint4 streaming read, 2.0 GiB | 2048 MiB | **239.5** | [238.9, 239.7, 239.5] |
| spec | LPDDR5X datasheet peak | - | 273.0 | - |

**Ceiling used below: 245.9 GB/s** (best of the three measured rulers; 90 % of the datasheet peak).

Three methods agree inside 3 %. A hand-written `__ldg`/`uint4` streaming reader does
**not** beat `torch.sum()`, so the ceiling is a property of the part, not of the
measuring method. **Ceiling = 245.9 GB/s.**

## Shape

Layout: 96 local experts, top-8, hidden K = 4096, per-rank intermediate N = 2048
(gate-up `[E, 2N, K]`, down `[E, K, N]`), NVFP4 weights (4-bit + block-16 FP8 e4m3
block scales + per-expert FP32 global scale). Two forms:
`all-8-local` (top-8 over the 96 local experts) and `EP-effective` (top-8 over 288
global experts, non-local dropped, remaining (token, expert) pairs run as topk=1) —
the second is the traffic an expert-parallel rank actually sees.
Measured resident weight bytes per expert are identical across all three quantised
arms (14,155,78x B = the 4-bit + fp8-scale model to 4 decimal places), so all
timing differences are efficiency, not byte count.

## Crossover

| form | routing | crossover M (best FP4 first beats W4A16-marlin by > 5 %) | best FP4 there |
|---|---|---|---|
| all-8-local | uniform | **1024** | b12x 0.73x |
| all-8-local | zipf s=1 | **1024** | b12x 0.93x |
| EP-effective | uniform | **4096** | b12x 0.86x |
| EP-effective | zipf s=1 | **1792** | b12x 0.85x |

Below the crossover, both FP4 paths are 1.03-1.16x **slower** than marlin W4A16.
Full per-M tables: [C1 to C4 above](#crossover--the-per-m-tables).

**One finding worth your attention:** in the all-8-local form marlin W4A16 has a
**cliff between M=512 and M=1024** — 6619 -> 10355 us (+56 %), achieved bandwidth
205 -> 131 GB/s, round-to-round spread 0.21 % and 1.89 % so it is not noise. The FP4
grouped GEMM does not have that cliff (6660 -> 7099 us). That cliff is where the whole
FP4 advantage comes from; it is a marlin scheduling artefact, not an FP4 tensor-core win.

## GEMM-only bound — the honest best case for a fused custom kernel

We timed the FP4 path **without** its activation-quantisation kernels: pre-quantised
activations, pre-built routing metadata (`get_cutlass_moe_mm_data`), no `shuffle_rows`,
no epilogue combine. What remains is the two grouped FP4 tensor-core GEMMs — the floor
no fused kernel can go below, because it must still stream the same weights and do the
same MACs.

| form | M | routing | marlin us | FP4 full us | **GEMM-only us** | act-quant us | meta+combine us | **GEMM-only / marlin** | GEMM-only GB/s |
|---|---|---|---|---|---|---|---|---|---|
| all-8-local | 8 | uniform | 2834 | 3107 | **3040** | 148 | 242 | **1.07x** | 218.9 |
| all-8-local | 64 | uniform | 5835 | 6394 | **6196** | 147 | 243 | **1.06x** | 219.3 |
| all-8-local | 128 | uniform | 5949 | 6585 | **6315** | 147 | 247 | **1.06x** | 215.2 |
| all-8-local | 256 | uniform | 5976 | 6969 | **6421** | 228 | 312 | **1.07x** | 211.6 |
| all-8-local | 512 | uniform | 6619 | 8001 | **6660** | 563 | 771 | **1.01x** | 204.1 |
| all-8-local | 1024 | uniform | 10355 | 9788 | **7099** | 1147 | 1532 | **0.69x** | 191.4 |
| all-8-local | 1792 | uniform | 13144 | 12461 | **7422** | 2052 | 2673 | **0.56x** | 183.1 |
| EP-effective | 8 | uniform | 1273 | 1381 | **1328** | 144 | 237 | **1.04x** | 213.2 |
| EP-effective | 64 | uniform | 5190 | 5678 | **5529** | 142 | 236 | **1.07x** | 220.2 |
| EP-effective | 128 | uniform | 5536 | 6089 | **5890** | 145 | 237 | **1.06x** | 218.7 |
| EP-effective | 256 | uniform | 6001 | 6463 | **6191** | 146 | 242 | **1.03x** | 217.2 |
| EP-effective | 1792 | uniform | 6909 | 8881 | **6765** | 798 | 1361 | **0.98x** | 200.9 |
| EP-effective | 1792 | zipf | 11823 | 12537 | **7618** | 1874 | 3166 | **0.64x** | 178.4 |

Two things follow, and they point in opposite directions:

1. **We owe you a correction.** At prefill sizes the non-GEMM plumbing is up to **38 %**
   of the FP4 path's wall time (M=1792 all-8-local: 2052 us act-quant + 2673 us
   metadata/combine out of 12461 us). Charging that to "the FP4 kernel" was unfair.
   Stripped down, the FP4 GEMM is **level with marlin** in the EP form at prefill
   (0.98x) instead of 29 % behind, and clearly ahead in the all-local form (0.56x).

2. **But it does not rescue the decode case.** At M <= 512 the activation-quantisation
   kernels cost only **142-148 us**, about 3 % of the call. Removing them entirely
   still leaves the FP4 GEMM at **1.02-1.10x marlin**. The bare FP4 grouped GEMM
   reaches **211-220 GB/s** where marlin's complete W4A16 path reaches **228-236 GB/s**
   on identical bytes — roughly **7 % less weight-streaming efficiency**. That gap,
   not fusion overhead, is what a custom sm_121 path would have to close first.

## Roofline

```
W       = experts_touched x measured bytes/expert
A       = (rows_in + rows_out) x K x 2      (bf16 in once, out once)
t_mem   = (W + A) / 245.9 GB/s
FLOPs   = pairs x 6 x N x K                 (logical, dequantised math)
t_fp4   = FLOPs / 500e12   (datasheet dense FP4 tensor peak, sm_121 - upper bound)
t_bf16  = FLOPs / 97.3e12  (our measured BF16 tensor throughput)
roofline_fp4 = max(t_mem, t_fp4)
```

| form | M | routing | t_mem us | t_fp4 us | roofline_fp4 us | marlin us | marlin % of roofline | best FP4 % of roofline | **honest ceiling (marlin / roofline_fp4)** |
|---|---|---|---|---|---|---|---|---|---|
| all-8-local | 8 | uniform | 2706 | 6 | 2706 | 2834 | 95 % | 91 % | **1.05x** |
| all-8-local | 64 | uniform | 5531 | 52 | 5531 | 5835 | 95 % | 89 % | **1.05x** |
| all-8-local | 128 | uniform | 5535 | 103 | 5535 | 5949 | 93 % | 88 % | **1.07x** |
| all-8-local | 256 | uniform | 5544 | 206 | 5544 | 5976 | 93 % | 88 % | **1.08x** |
| all-8-local | 1792 | uniform | 5646 | 1443 | 5646 | 13144 | 56 % | 59 % | **2.33x** |
| EP-effective | 8 | uniform | 1153 | 2 | 1153 | 1273 | 91 % | 89 % | **1.10x** |
| EP-effective | 64 | uniform | 4964 | 19 | 4964 | 5190 | 96 % | 90 % | **1.05x** |
| EP-effective | 128 | uniform | 5262 | 35 | 5262 | 5536 | 95 % | 90 % | **1.05x** |
| EP-effective | 256 | uniform | 5513 | 67 | 5513 | 6001 | 92 % | 91 % | **1.09x** |
| EP-effective | 1792 | uniform | 5850 | 489 | 5850 | 6909 | 85 % | 81 % | **1.18x** |

Note `t_fp4` versus `t_mem`: even with a 500 TFLOPS FP4 peak, the arithmetic never
becomes the binding constraint below M ~ 4096 in either form. At M=64 the MoE stage
needs 52 us of FP4 math and 5531 us of DRAM. **There is no idle math to sell there.**

## Pre-registered verdict, per operating point

Criterion A: GEMM-only FP4 beats marlin by > 10 %. Criterion B: roofline_fp4 more than
1.25x faster than measured marlin. SUPPORTED if A or B; otherwise NOT SUPPORTED.
Criteria and thresholds were written before the run.

| operating point | form | A | B | max gain of a custom path | verdict |
|---|---|---|---|---|---|
| M=8 (1 stream, 7 draft tokens) | all-8-local | 1.07 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=8 | EP-effective | 1.04 no | 1.10 no | 9 % | NOT SUPPORTED |
| M=64 (8 streams) | all-8-local | 1.06 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=64 | EP-effective | 1.07 no | 1.05 no | 4 % | NOT SUPPORTED |
| M=128 (16 streams) | all-8-local | 1.06 no | 1.07 no | 7 % | NOT SUPPORTED |
| M=128 | EP-effective | 1.06 no | 1.05 no | 5 % | NOT SUPPORTED |
| M=256 (32 streams) | all-8-local | 1.07 no | 1.08 no | 7 % | NOT SUPPORTED |
| M=256 | EP-effective | 1.03 no | 1.09 no | 8 % | NOT SUPPORTED |
| M=1792 (prefill chunk) | all-8-local | **0.56 YES** | **2.33 YES** | **57 %** | **SUPPORTED** |
| M=1792 (prefill chunk) | EP-effective | 0.98 no | 1.18 no | 15 % | NOT SUPPORTED |
| M=1792, zipf s=1 | EP-effective | **0.64 YES** | **1.88 YES** | **47 %** | **SUPPORTED** |

**Summary.** The theory is supported at prefill in the non-EP form, and in the EP form
only under a hot-expert skew that leaves one rank holding ~80 % of the pairs. At every
decode and concurrency point it is not supported: those points are 88-96 % of an
absolute roofline that no kernel can beat, and even a zero-overhead FP4 GEMM measures
slower there than the W4A16 path it would replace.

## Two concrete, checkable things

1. **Weight-streaming efficiency at memory-bound sizes.** On identical byte counts the
   FP4 grouped GEMM sustains 211-220 GB/s where W4A16 marlin sustains 228-236 GB/s
   (89 % vs 95 % of a 245.9 GB/s ceiling). Closing that ~7 % is worth more at our
   operating points than any tensor-core work, and it is measurable without a model.
2. **Wrapper sizing.** Building the SM12x MoE wrapper with `max_num_tokens` matched to
   the actual batch rather than a fixed upper bound is worth **8.8 % at M=128 and
   9.6 % at M=256** in the all-8-local form (no measurable effect in the EP form, and
   <= 2 % at every other M). Integrations that construct the wrapper once from
   `max_num_batched_tokens` leave that on the table.

---

## Raw

[`gb10-crossover/`](gb10-crossover/) — 134 KB, **unedited**. Nothing in it needed scrubbing: every
path it contains is inside the container. Container timestamps are UTC; the run times quoted on this
page are Istanbul.

| File | What it is |
|---|---|
| `ruler.json` | the three rulers, all rounds, the chosen ceiling, device properties |
| `marlin.json`, `cutlass.json` | one record per (form, M, routing): all three rounds, median, spread, rows, pairs, experts touched, measured weight bytes, GB/s, TFLOPS; plus the arm's build time, set bytes, measured bytes per expert, GPU peak and host `MemAvailable` floor |
| `b12x-matched.json`, `b12x-fixed2k.json` | the same for b12x, with `max_num_tokens` matched to M and fixed at ≥ 2,048 — the A/B in table A1 |
| `split.json` | the decomposition: `us_full`, `us_gemm_only`, `us_actquant`, `us_meta_combine` and the residual per point |
| `bf16.json` | the bf16 reference arm |
| `smoke-*.json`, `smoke-*.log`, `SMOKE.log` | the two-minute API smoke pass before the sweep: every arm constructs and returns. Three points each, not a measurement |
| `*.log` | per-arm stdout, one line per measured point |
| `RUN.log`, `RUN2.log` | the driver transcripts, including the two `rc=137` watchdog kills at M = 4,096 and the capped re-run |
| `watchdog.log` | the host-memory watchdog's own record: the readings below 6 GiB and the two container kills |
| `telemetry.csv` | `unix_ts, temp_C, power_W, sm_clock, max_sm_clock, gpu_util, clocks_event_reasons_active, MemAvailable_kB` every 5 s |

Regenerate the tables from these files:

```bash
XSWEEP_OUT=results/kernels/gb10-crossover python3 bench/moe-kernels/crossover/agg-crossover.py
```
