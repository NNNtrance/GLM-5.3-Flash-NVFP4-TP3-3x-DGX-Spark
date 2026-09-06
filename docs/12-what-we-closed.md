# 12 — What we closed, with the numbers

[08 — What we tried and rejected](08-what-we-tried.md) is the catalogue of branches we opened and
closed on the way to the production setup. This document is narrower and later: it is the list of
**levers** somebody will otherwise try on this stack, each one with the measurement that closes it.
Several of them are things that worked on our EXL3 stack, or that are standard advice for GPU
serving, and are dead here for reasons specific to this hardware and this quantization scope.

Measured 2026-09-05/06 on the candidate configuration of
[10](10-production-candidate-and-lessons.md) unless a section says otherwise. The step breakdowns
referenced throughout are in [11 — Measured profile](11-measured-profile.md). Noise band
C1 ±4 · C2 ±6 · C4 ±9 · C6 ±6 · C8 ±3 %.

---

## 1. Summary

| # | Lever | Verdict | The number that closes it |
|---|---|---|---|
| 1 | CUDA-graph memory estimator off | **Rejected** | +9.3 % pool, but 3.54 GiB swap on rank 0 and C8 −3.8 % (outside band) |
| 2 | `NCCL_MAX_NCHANNELS=8` | **Taken, and an earlier verdict retracted** | dropping it costs C4 −7.7 %, C6 −9.7 % |
| 3 | GPU clock lock / DVFS / power / cooling | **Closed** | throttle flag 0 in 1,478 samples; lock is accepted and changes nothing |
| 4 | CPU idle states off | **Closed** | 0.8 % and 0 % against time-adjacent arms; the apparent +4.8 % was drift |
| 5 | `posix_fadvise` + `malloc_trim` after load | **Closed** | KV +0.2 % (it was +4.1 % on the sibling line) |
| 6 | `--max-num-batched-tokens` granularity | **Closed as a lever** | the real chunk is 2,041 of 2,048 = 99.7 % |
| 7 | CPU gap / launch overhead | **Closed** | occupancy 98.1–99.2 %; gap 0.85–2.4 % |
| 8 | A better MoE kernel than marlin | **Closed** | marlin is at 92–99 % of the DRAM ruler at decode |
| 9 | Dropping expert parallelism (TP-2304 layout) | **Closed** | 1.16–1.26× slower, +12.52 % weight bytes per rank |
| 10 | Memory fraction above 0.88 | **Closed in this configuration** | +3.9 GiB of budget put rank 0 into 3.54 GiB of swap |
| 11 | fp32 strided-batched MLA GEMM → bf16 | **Closed as negligible** | 0.053 ms of a C1 forward pass, ≈ +0.3 % |
| 12 | Quantising the bf16 dense path | **Not done — owner's quality call** | best case: C1 parity with our EXL3 stack, C8 and KV still ~15 % behind |

---

## 2. The CUDA-graph memory estimator flag

**What it is.** At every boot vLLM prints
`--gpu-memory-utilization=0.8800 is equivalent to 0.8480 without CUDA graph memory profiling … To
disable, set VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0`. The estimator reserves budget for a CUDA
graph pool it has not yet allocated. On this stack it reserves **~3.9 GiB** while the pool that
actually gets allocated is **0.0–0.5 GiB**, and the estimate swings about 2× from boot to boot. The
pool is sized from the **worst rank's** estimate. This flag is not set anywhere in our trees; the
behaviour is the estimator's own.

**The flag works, and the mechanism is unambiguous.** With it set to 0 the "equivalent to" line is
never printed — the cleanest possible evidence — and the accounting shows where the reservation was
living: the estimator writes it into the **peak activation** term.

| Term, per rank (head / worker-1 / worker-2) | estimator on | estimator off | Δ |
|---|---|---|---|
| Peak activation (GiB) | 5.80 / 5.85 / 5.80 | **1.89 / 1.89 / 1.89** | **−3.9** |
| CUDA graph pool, actual (GiB) | 0.28 / 0.08 / −0.01 | 0.01 / 0.44 / 0.46 | ≈0 |
| CUDA graph pool, estimated (GiB) | 3.90 / 3.96 / — | 3.76 / 3.73 / 3.65 | ≈0 (still computed) |
| KV cache in use (GiB) | 35.59 / 35.34 / 35.57 | **39.15 / 39.07 / 39.18** | **+3.5** |
| Consumed (weights + non-torch, GiB) | 65.65 / 65.84 / 65.66 | 65.99 / 66.06 / 65.96 | +0.3 |
| **KV pool (tokens)** | 4,301,449 | **4,756,521** | **+9.3 %** |

**Rejected, on two independent grounds.**

| Cost | estimator on | estimator off | Verdict |
|---|---|---|---|
| Swap under load, rank 0 | 0.03 GiB | **3.54 GiB** | **memory rule violated** |
| MemAvailable min, worst node | 3.19 GiB | 2.24 GiB | worse |
| C1 aggregate | 51.2 | 50.3 | −1.8 %, inside band |
| C4 aggregate | 99.5 | 100.1 | equal |
| **C8 aggregate** (against 151.2 on the equivalent 8-channel arm) | 147.5 | **145.4** | **−3.8 %, outside band** |
| Code gate, cold / repeat | 11/12 · warm 12/12 | **11/12 · repeat 11/12, same item** | **gate failed** |
| Boot | 250 s | 281 s | +12 % |

Rank 0 goes 3.54 GiB into swap during boot (0.02 GiB before, back to 0.03 when the engine stops — so
the swap is the engine's), and the code exam dropped the same problem on the cold run **and** on the
repeat, which is our rule for failing an arm. Even setting the gate aside, the trade is bad: +9.3 %
of KV pool paid for with an out-of-band speed loss at the concurrency the cluster is built for.
`[measured-here, raw not published]`, 2026-09-06.

**A misattribution of ours, corrected in place.** `NVRM … NV_ERR_NO_MEMORY` lines in the kernel log
were first blamed on this flag. `dmesg` history shows them at **every** boot that night — seven of
them, including the untouched baseline and a session with no engine at all. They are a boot artefact
of this stack at fraction 0.88. The only memory cost attributable to the flag is the swap.

**What stays open.** The estimator is still charging ~3.9 GiB of a budget it does not use, and there
is no supported way to reclaim that without turning off a profiler that is evidently doing something
useful for stability. On our EXL3 stack the same estimator charges **0.00 GiB** — this is not a
platform property, it is a property of this engine build's graph configuration.

---

## 3. GPU clock lock, DVFS, power and cooling

**Telemetry**, 1,478 samples × 3 nodes at a 5 s period, spanning the whole session:

| Window | Node | GPU temp min/avg/max (°C) | Power min/avg/max (W) | SM clock min/avg/max (MHz) | % of the 3,003 ceiling | GPU util avg | Throttle rows |
|---|---|---|---|---|---|---|---|
| sweep A | head | 67 / 70.5 / 73 | 37.9 / 42.2 / 49.3 | 2,463 / 2,493 / 2,509 | 83.0 % | 96 % | **0 / 182** |
| | worker-1 | 59 / 60.9 / 63 | 23.2 / 38.8 / 42.9 | 2,496 / 2,529 / 2,554 | 84.2 % | 95 % | **0 / 183** |
| | worker-2 | 62 / 67.3 / 70 | 27.0 / 42.1 / 49.2 | 2,411 / 2,433 / 2,437 | 81.0 % | 94 % | **0 / 183** |
| sweep B | head | 64 / 69.1 / 71 | 24.9 / 41.7 / 48.5 | 2,411 / 2,494 / 2,509 | 83.1 % | 95 % | **0 / 188** |
| | worker-1 | 59 / 60.9 / 63 | 23.3 / 38.8 / 42.3 | 2,489 / 2,529 / 2,554 | 84.2 % | 95 % | **0 / 188** |
| | worker-2 | 63 / 66.9 / 69 | 37.5 / 42.2 / 46.8 | 2,424 / 2,433 / 2,437 | 81.0 % | 95 % | **0 / 189** |

**The throttle flag never lit — not once in 1,478 samples.** CPU side is flat in every window: core
average a constant 3,354,000 kHz.

**The clock lock A/B.** `nvidia-smi -lgc 3003,3003` is **accepted** on all three nodes
(`GPU clocks set to "(gpuClkMin 3003, gpuClkMax 3003)"`). The requested clock is then **never
reached**:

| Arm | C1 agg tok/s | C8 agg tok/s | Actual SM clock (MHz) | Power avg (W) | GPU temp avg (°C) |
|---|---|---|---|---|---|
| default, 3 rounds | 49.7 | 149.8 | 2,500 / 2,535 / 2,438 | 40.8 / 37.7 / 40.6 | 68.0 / 58.7 / 64.3 |
| **locked 3003, 3 rounds** | **50.3** | **147.0** | **2,508 / 2,548 / 2,437** | 42.6 / 39.6 / 41.8 | 69.7 / 61.4 / 66.7 |
| default again, 1 round | 51.7 | 148.3 | back to the same plateau | — | — |
| **Δ locked vs default** | **+1.2 %** (band ±4) | **−1.9 %** (band ±3) | **+0.3 / +0.5 / −0.04 %** | +1.2 / +1.9 / +1.2 W | +1.7 / +2.7 / +2.4 °C |

**DVFS is not a bottleneck on this stack, and the topic is closed.** Locking leaves speed inside the
band, does not actually change the clock, and costs 1–2 W and about 2 °C. Each node has its own fixed
plateau — 2,500, 2,535 and 2,437 MHz, i.e. 81–85 % of the ceiling — and that plateau is the same
locked or unlocked. `[measured-here, raw not published]`, 2026-09-06.

**An independent source agrees**, which is why we treat this as settled rather than as one reading:
the published GB10 driver findings in
[`QuixiAI/open-gpu-kernel-modules`](https://github.com/QuixiAI/open-gpu-kernel-modules)
(`driver_improvement_findings.md`) report that `-lgc` is accepted, that the part tops out near
2,580 MHz, that a decode load runs at 2,430–2,580 MHz and 20–40 W, and that the ~90 W limit lives in
GSP firmware and is not user-accessible. Our measured plateau (2,437–2,554 MHz) and power (38–50 W
peak, 38–43 W average) sit inside that. `[reported]`

This also bears on [09 item 2](09-open-problems.md#2-one-node-is-about-25--slower-permanently-and-its-fan-does-not-spin-at-idle) — one node
being permanently a couple of per cent slower. The per-node plateaus above (2,437 against 2,535 MHz,
a 3.9 % spread) reproduce the earlier sustained-clock reading (2,502 / 2,485 / 2,440 MHz), and the
lock A/B now says there is nothing to be done about it from software. The item stays open as to
*cause*; it is closed as to *remedy*.

---

## 4. CPU idle states

Inventory, recorded on all three nodes before anything was changed and identical across them: driver
`acpi_idle`, governor `menu`, 20 cores, states **LPI-0 / LPI-1 / LPI-2 / LPI-3**, exit latencies
**0 / 42 / 231 / 433 µs** — matching the LPI-2 / LPI-3 figures in the independent source above
exactly.

| Arm | C1 agg tok/s | C8 agg tok/s | prefill fresh (tok/s) |
|---|---|---|---|
| states 1–3 enabled, 03:51 (3 rounds) | 49.7 | 149.8 | — |
| states 1–3 enabled, 04:04 (1 round, time-adjacent) | 51.7 | 148.3 | — |
| **states 1–3 disabled, 04:07 (3 rounds)** | **52.1** | **150.5** | **1,929** |
| states 1–3 re-enabled, 04:14 (2 rounds) | **52.1** | 148.0 | 1,923 |

At first reading this is 49.7 → 52.1 = **+4.8 %**, outside the band. **The return leg killed it:**
when the states were re-enabled the number did not go back to 49.7, it **stayed at 52.1**. What we
had measured was the post-boot upward drift of C1 described in
[10 §6](10-production-candidate-and-lessons.md#6-measurement-protocol-corrections-made-during-this-work).
Against time-adjacent arms the effect is 51.7 → 52.1 → 52.1, i.e. **+0.8 % and 0 %**, well inside the
band, and prefill is 1,929 against 1,923 — equal.

**Disabling CPU idle states buys nothing measurable on this workload.** The setting was reverted (80
files per node verified back at `disable=0`) and no configuration file was ever written.
`[measured-here, raw not published]`, 2026-09-06.

The durable output of this arm is not the verdict, it is the method: **run every system-setting A/B
with a return leg**, and compare C1 only between boots of the same age.

---

## 5. `posix_fadvise(DONTNEED)` + `malloc_trim` after weight load

KV pool +0.2 % here, against +4.1 % on the sibling line. Full numbers and the mechanism —
`instanttensor` leaves no allocator arena to reclaim, so `malloc_trim` gives back 60 MB instead of
2.7 GiB — are in
[10 §2.4](10-production-candidate-and-lessons.md#24-posix_fadvisedontneed--malloc_trim--did-not-carry-over).
Harmless, worth keeping for its 16 s of boot time, not a memory lever.

The same reasoning closes two neighbours without a measurement: `--enable-ep-weight-filter` and
per-rank load sidecars are **no-ops under `instanttensor`**, which already reads the NVFP4 weights in
57 s. `[not tested]`, by inspection of the loader path.

---

## 6. Prefill chunk granularity

The profile shows a steady prefill chunk of **2,041 tokens against a `--max-num-batched-tokens` of
2,048 — 99.7 % of the budget** ([11 §3](11-measured-profile.md#3-prefill--a-steady-2041-token-chunk)).
On our EXL3 stack the same measurement reads 87.5 %, and there the chunking was worth attention.
Here the budget is not being wasted, so there is no granularity lever to pull, and raising the budget
would cost KV pool.

This **narrows but does not close**
[09 item 8](09-open-problems.md#8---max-num-batched-tokens-2048-against-4096): a single-variable A/B
of 2048 against 4096 has still never been run on this stack. What is now known is that the smaller
setting is not leaving budget on the floor. `[measured-here, raw not published]`, 2026-09-06.

---

## 7. CPU gap and launch overhead

GPU idle time inside a step: **1.9–2.4 % at C1, 0.9–1.1 % at C8, 0.85 % at prefill**, with occupancy
at **98.1–99.2 %**. There is at most 2 % to win and it is spread over hundreds of launches. Closed.
`[measured-here, raw not published]`, 2026-09-06.

---

## 8. A better MoE kernel

Closed by two model-free studies already in this repository —
[`results/kernels/moe-kernel-bench-gb10.md`](../results/kernels/moe-kernel-bench-gb10.md) and its
adversarial re-run [`results/kernels/fp4-crossover-sweep-gb10.md`](../results/kernels/fp4-crossover-sweep-gb10.md)
— and reinforced by the profile. At decode batch sizes marlin runs at **92–99 % of measured DRAM
bandwidth**, a zero-overhead FP4 GEMM is **1.03–1.07× slower** at the production shapes, and the
crossover where an FP4 path would win sits at M ≥ 1,792, above the traffic this recipe runs. Since
MoE is **62.4 % of a C8 step**, this is simultaneously the largest term in the profile and the one
with the least headroom.

**The only remaining route at C8 is fewer bytes — a smaller expert format — not a faster kernel.**

---

## 9. Dropping expert parallelism

Recorded as [08 item 35](08-what-we-tried.md). At equal token traffic and balanced routing the no-EP
TP-2304 layout is **1.16–1.26× slower** at every batch size measured and wants **+12.52 %**
routed-expert weight bytes per rank (1.424 against 1.266 GiB per set), which comes straight out of
the KV pool. Its communication side is explicitly **unmeasured** — the bench is single-GPU — but the
byte cost does not depend on it.

---

## 10. Memory fraction above 0.88

The estimator experiment in §2 is the proxy for this question, and it answers it: putting another
3.9 GiB of budget into play pushed rank 0 into **3.54 GiB of swap**. There is no room above 0.88 in
this configuration. `[measured-here, raw not published]`, 2026-09-06.

It becomes reopenable only if the weight side gets lighter — which is what §12 would have done.

---

## 11. The fp32 strided-batched MLA GEMM

`gemmSN_TN_kernel<float,…>` is **0.053 ms of a C1 forward pass**, about 0.9 % of the dense row and
roughly **+0.3 %** end to end if it were moved to bf16. Real, tiny, and the same conclusion was
reached independently on our EXL3 stack. Recorded so nobody spends a day on it.
`[measured-here, raw not published]`, 2026-09-06.

---

## 12. Quantising the bf16 dense path — the one open lever, deliberately not taken

This is the largest single item the profile exposes: **bf16 dense linears are 49.4 % of a C1 step**.
On our EXL3 stack the same class was 45 %, and quantising it was worth **+23 %** single stream.

**It was not done, and the reason is the owner's, not the measurement's: quality risk, for a cluster
whose goal is not only speed.** Decision recorded 2026-09-06. `[not tested]`

The supporting analysis, which is why nobody should reopen it expecting a different answer:

- **The projection.** Anchored on two figures measured on this hardware — marlin NVFP4 W4A16 takes
  **0.281×** the time of bf16 at M = 8, exactly the theoretical byte ratio, and marlin already
  attains 92–99 % of the DRAM ruler — a fully quantized dense path reaches roughly **C1 65–70 tok/s,
  C8 163–168, KV ~4.8 M**. Our EXL3 stack on the same nodes is at **70.5 / 194.0 / 5,619,834**. Best
  case that is **parity at C1** and **~15 % behind at C8 and on the pool**, because C8 is 62 % MoE
  and MoE is at the memory wall. `[estimate]`
- **The structural block.** vLLM fuses five checkpoint tensors into one `in_proj_qkvgfab` module and
  requires every shard of a fused module to carry the same `quant_algo`. Quantising
  `q_proj`/`k_proj`/`v_proj` — 41.8 % of dense bytes — therefore forces two KDA gating arms to 4 bits
  with them, and those are exactly the tensors the EXL3 checkpoint's author left in bf16 for
  sensitivity reasons. All or nothing, on the only part worth doing.
  ([11 §8](11-measured-profile.md#8-why-the-dense-half-was-not-quantized).)
- **The staging is worse than the byte table suggests.** The trace attribution says the recoverable
  serial dense time is 32.3 ms of the ~51, that **68 % of it is KDA**, and that the shared expert —
  12.9 % of dense bytes — is hidden under marlin and would buy nothing. The cheap, low-risk families
  are close to worthless; the reward is entirely in the family with the fusion block and the quality
  risk. ([11 §7.1](11-measured-profile.md#71-a-second-pass-over-the-same-traces-and-what-it-changed).)

**Read the cross-line comparison with these four caveats, which are not decoration.**

1. **The memory fractions are not the same** — 0.83 on the EXL3 side against 0.88 here. That
   correction runs **against** this recipe: the other stack produces a larger KV pool at a lower
   fraction because its checkpoint is lighter in memory (58.3–59.1 GiB consumed against 65.7), so
   equalising the setting would widen the KV gap, not close it.
2. **Total and per-stream are separate rows and were never mixed.** Confusing them cost the sibling
   line a published error once.
3. **The EXL3 numbers come from the old prompt rotation.** They were not re-measured with the
   corrected sweep runner ([10 §6](10-production-candidate-and-lessons.md#6-measurement-protocol-corrections-made-during-this-work)),
   which makes their C1 and C2 medians the **weakest link** in the comparison; C4–C8 are less
   affected. This is the single largest reason to treat the cross-line table as indicative rather
   than decisive.
4. **The comparison is between two scopes, not two formats.** The EXL3 arm quantizes the dense
   linears too; this one quantizes routed experts only. That is exactly what §12 is about, and it is
   why "NVFP4 is slower than EXL3" would be the wrong reading of these numbers.

What is **not** claimed here: that quantising the dense path would fail, or that it would not deliver
the C1 gain. The C1 gain is well supported. The claim is only that the finish line is behind where
the other line already stands, and that the price is paid in a part of the model nobody has measured
the quality of at 4 bits.

---

## 13. What opened while these closed

Recorded here rather than in [09](09-open-problems.md) because they arrived with this work and none
of them has been worked on.

- **KDA / GDN linear attention at concurrency.** 2.5 % of a C1 step but **7.2 % of a C8 step**
  (19.6 ms) — it grows 7.5× where everything else shrinks. After dense it is the third-largest class
  at C8 and it has never been looked at. `[measured-here, raw not published]`
- **The KDA GEMM leaves 37 % on the floor.** Standalone on an idle node, at the production shapes, it
  sustains 214 GB/s; inside the engine, 150 GB/s. Alignment explains 3–5 % of that. Not diagnosed.
  A cuBLAS kernel-selection fix in the same family was estimated at **+3 % C1** and never applied.
  `[measured-here, raw not published]`
- **Layer 45 (MTP) is loaded and never used.** Production speculates from a separate draft
  checkpoint, so ~7.84 GB (≈2.6 GiB per rank) sits resident and idle. Not loading it is arithmetically
  worth about **+7 % KV pool** for a one-line loader filter — larger than everything in §12 — and its
  feasibility was never checked. `[not tested]`
- **Single-stream C1 drifts upward for ~30 minutes after a boot.** Cause not found; prefix-cache
  filling is the obvious candidate. Recorded as a measurement rule rather than a phenomenon.
  `[measured-here, raw not published]`
- **The head node's ACPI thermal zone peaks at 94–96 °C** against 76–89 on the others. No throttle
  event was ever recorded and clocks do not drop. Component not identified, long-term effect not
  investigated. `[measured-here, raw not published]`
