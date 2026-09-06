# 11 — Where a step actually goes: the measured profile

A kernel-level breakdown of one prefill chunk and one decode step, at C1 and at C8, on all three
ranks. It is the document that explains why most of the levers in
[12 — What we closed](12-what-we-closed.md) are closed, and why the one lever that is genuinely open
was still not taken.

Measured on **2026-09-06** with the candidate configuration of
[10](10-production-candidate-and-lessons.md) plus `--profiler-config`; the launch command differs from
the candidate by exactly that one token. Same image, same checkpoint, same draft. KV pool
**4,347,826** tokens, identical to the candidate's; boot 244 s. The engine was not reconfigured —
only `/start_profile`, `/stop_profile` (both HTTP 200) and ordinary chat requests were used, and a
rank-named trace landed on all three nodes. `[measured-here, raw not published]`

> **Profiling costs host RAM.** The kineto buffers push host `MemAvailable` down hard. Restart the
> engine after a profiling session before you trust its steady-state memory numbers.

---

## 1. What the profiler itself costs, measured in the same boot

| Window | Profiler off | Profiler on | Overhead |
|---|---|---|---|
| Prefill, fresh ~8.3k prompt, `max_tokens=1` | 1,932 / 1,937 / 1,935 tok/s | 1,908 tok/s | **+1.4 %** |
| Decode C1, ms per step | 98.92 / 98.91 → **98.9** | **101.14** | **+2.3 %** |
| Decode C8, ms per step | 258.26 / 262.52 → **260.4** | **266.87** | **+2.5 %** |

CUPTI timestamps kernels **on the device**, so kernel durations are not distorted; the overhead lands
on the CPU-gap and NCCL rows. As a shared upper bound for the tables below: at C1, NCCL + CPU gap
together are **≤ 15.2 ms** rather than the 17.5 ms measured in-span; at C8, **≤ 20.9 ms** rather than
27.4 ms; at prefill every class carries ±1.4 %.

For scale: the same measurement on our EXL3 stack costs **+16.4 %** at C1. This stack launches far
fewer kernels per step, so its profile is much closer to its unprofiled self.

---

## 2. Method, and the two traps we walked into

**Step boundary.** The annotation-based step split used on the sibling line
(`execute_context_N(T)_generation_M(T)`, overlapping regions merged) corresponds here to **a forward
pass, not an engine step**: 99 engine steps produced 816 merged regions, about 8.2 per step — the
target verification plus the speculative draft passes. Shares were therefore computed **two
independent ways** and the two agreed: (a) the annotation-based analyser, and (b) a second script
that divides the whole window by the engine step count taken from
`vllm:spec_decode_num_drafts_total`. **The tables below are from (b).**

**Taxonomy trap, caught before the tables were read.** The b12x tilelang kernels are named
`kernel_cutlass_kernel_b12x…`. A naive "contains cutlass" rule swallows the attention and
hidden-channel mixing kernels into the dense GEMM row. Real dense GEMMs on this stack look like
`void cutlass::Kernel2<cutlass_80_…gemm…>`. The rule was bound to that, the b12x rules were moved
ahead of it, and the contents of the "Norm / elementwise / other" bucket were audited separately in
all three windows — all genuinely elementwise, copy or norm work, with no misfiled GEMM or attention
kernel.

Spread across the three ranks is ≤ 0.8 percentage points in every class in every window, which is the
main reason we believe the reading.

---

## 3. Prefill — a steady 2,041-token chunk

Wall 1,011.6 / 998.2 / 998.2 ms · GPU busy 99.1–99.2 % · CPU gap 8.5–8.8 ms (0.84–0.88 %) ·
draft-model tail 16.3–17.4 ms (1.63–1.74 %). Cost per token **0.4956 ms** (1,011.6 ms / 2,041), i.e.
2,017 tok/s inside the chunk. Chunk shapes seen in the window: 2,041 ×2, 1,613 ×1, 1,287 ×2.

| Class | rank 0 (ms) | rank 1 | rank 2 | % of chunk (r0 / r1 / r2) | Calls per chunk |
|---|---:|---:|---:|---|---:|
| **MoE marlin GEMM** | 301.7 | 307.5 | 310.5 | **29.83 / 30.81 / 31.10** | 84 |
| **BF16 dense GEMM** | 191.4 | 189.9 | 192.2 | **18.92 / 19.02 / 19.26** | 449 |
| NCCL collectives | 140.2 | 127.7 | 118.4 | 13.86 / 12.79 / 11.86 | 105 |
| B12X hidden-channel mixing | 125.3 | 123.0 | 122.9 | 12.39 / 12.33 / 12.32 | 275 |
| Norm / elementwise / other | 92.3 | 90.6 | 91.3 | 9.12 / 9.08 / 9.14 | 1,160 |
| B12X / MLA attention | 81.1 | 79.3 | 83.1 | 8.02 / 7.95 / 8.32 | 81 |
| KDA / GDN linear attention | 38.6 | 38.0 | 38.3 | 3.82 / 3.80 / 3.84 | 218 |
| MoE route / align / sum / act | 24.6 | 25.9 | 25.3 | 2.43 / 2.60 / 2.53 | 210 |
| memcpy / memset | 8.4 | 8.0 | 8.3 | 0.84 / 0.80 / 0.83 | 124 |
| CPU gap (GPU idle) | 8.5 | 8.8 | 8.5 | 0.84 / 0.88 / 0.85 | — |
| **Total (wall)** | **1,011.6** | **998.2** | **998.2** | 100 | — |

At prefill's larger M the dense GEMM goes to different kernels than at decode:
`cutlass_80_tensorop_bf16_s16816gemm` 108.1 ms (56 % of dense), six distinct
`nvjet_sm121_tst_mma_*` kernels 65.7 ms, `cutlass_80_wmma` 12.8 ms.

**The chunk is 2,041 tokens against a `--max-num-batched-tokens` of 2,048 — 99.7 % of the budget.**
On the sibling line the same measurement reads 87.5 %. That closes a question this recipe had left
open: the prefill budget is not being wasted here.

---

## 4. Decode at C1 — 99 engine steps, 103.3 ms per step in-span (98.9 unprofiled)

Occupancy 98.1 %. Concurrent-stream overlap (sum of kernel durations minus their union)
12.8 ms per step.

| Class | rank 0 (ms) | rank 1 | rank 2 | % of step (r0 / r1 / r2) | Calls per step |
|---|---:|---:|---:|---|---:|
| **BF16 dense GEMM** | **50.98** | **51.09** | **51.03** | **49.37 / 49.49 / 49.43** | 587 |
| **MoE marlin GEMM** | 39.60 | 40.11 | 40.37 | **38.35 / 38.85 / 39.10** | 86.5 |
| NCCL collectives | 15.55 | 14.76 | 14.27 | 15.06 / 14.29 / 13.82 | 108 |
| KDA / GDN linear attention | 2.60 | 2.57 | 2.59 | 2.51 / 2.49 / 2.50 | 154 |
| B12X hidden-channel mixing | 1.99 | 1.98 | 2.00 | 1.93 / 1.92 / 1.93 | 192 |
| B12X / MLA attention | 1.20 | 1.17 | 1.22 | 1.16 / 1.14 / 1.18 | 106 |
| Norm / elementwise / other | 1.12 | 1.09 | 1.12 | 1.08 / 1.06 / 1.09 | 617 |
| MoE route / align / sum / act | 0.93 | 0.92 | 0.94 | 0.90 / 0.89 / 0.91 | 216 |
| Sampling / speculation bookkeeping | 0.07 | 0.07 | 0.07 | 0.07 | 14 |
| memcpy / memset | 0.06 | 0.06 | 0.07 | 0.06 | 43 |
| CPU gap (GPU idle) | 1.99 | 2.33 | 2.45 | 1.93 / 2.25 / 2.37 | — |
| **Total (wall)** | **103.26** | **103.24** | **103.25** | 100 | — |

Inside the dense row (rank 0, ms per forward pass):
`cutlass_80_wmma_tensorop_bf16_s161616gemm` **6.111 of 6.199 — 98.6 %**;
`gemmSN_TN_kernel<float,…>` 0.053 (the fp32 strided-batched cuBLAS GEMM in the MLA family);
`cublasLt::splitKreduce` 0.035.

---

## 5. Decode at C8 — 60 engine steps, 271.8 ms per step in-span (260.4 unprofiled)

Occupancy 98.9–99.1 %.

| Class | rank 0 (ms) | rank 1 | rank 2 | % of step (r0 / r1 / r2) | Calls per step |
|---|---:|---:|---:|---|---:|
| **MoE marlin GEMM** | **169.95** | **169.46** | **169.17** | **62.53 / 62.36 / 62.25** | 87 |
| **BF16 dense GEMM** | 47.76 | 47.27 | 47.38 | **17.57 / 17.40 / 17.44** | 565 |
| NCCL collectives | 24.83 | 25.55 | 25.02 | 9.14 / 9.40 / 9.21 | 108 |
| KDA / GDN linear attention | 19.55 | 19.58 | 19.80 | 7.19 / 7.20 / 7.28 | 155 |
| B12X hidden-channel mixing | 6.14 | 6.05 | 6.25 | 2.26 / 2.23 / 2.30 | 192 |
| B12X / MLA attention | 2.63 | 2.59 | 2.69 | 0.97 / 0.95 / 0.99 | 288 |
| Norm / elementwise / other | 2.17 | 2.15 | 2.19 | 0.80 / 0.79 / 0.81 | 619 |
| MoE route / align / sum / act | 1.52 | 1.53 | 1.56 | 0.56 / 0.56 / 0.57 | 217 |
| Sampling / speculation bookkeeping | 0.15 | 0.15 | 0.15 | 0.05 | 14 |
| memcpy / memset | 0.12 | 0.10 | 0.11 | 0.04 | 140 |
| CPU gap (GPU idle) | 2.37 | 2.71 | 2.89 | 0.87 / 1.00 / 1.06 | — |
| **Total (wall)** | **271.78** | **271.73** | **271.76** | 100 | — |

---

## 6. The three numbers this document exists for

> **BF16 dense linears are 49.4 % of a C1 decode step, 17.5 % of a C8 step, and 19.1 % of a prefill
> chunk. MoE marlin is 38.4 % of C1, 62.4 % of C8, 30.6 % of prefill.**

That is the whole shape of this engine in one line, and it explains the split personality of every
optimisation question asked of it:

- **Single stream is a dense-weight problem.** Half the step is spent streaming bf16 weights that
  this checkpoint left unquantized. On our EXL3 stack the same class was 45 % of a step, and
  quantising it was worth **+23 %** single stream.
- **Concurrency is a MoE problem, and MoE is already finished.** At C8, 62 % of the step is in
  marlin, and our own model-free bench measured that kernel at **92–99 % of the memory bandwidth
  ruler** at decode batch sizes — see [`results/kernels/`](../results/kernels/). There is no idle
  arithmetic there for a better kernel to sell. The only remaining route at C8 is *fewer bytes*, not
  a faster kernel.
- **The interconnect is a small, closed term.** 13.8–15.1 % of C1, 9.2 % of C8, 11.9–13.9 % of
  prefill — and both ends of that range are already against physical limits
  ([10 §4](10-production-candidate-and-lessons.md#4-fabric-facts-that-apply-to-any-recipe-on-this-hardware)).

---

## 7. Which families the dense half is made of

The profile measures dense as a **class**. To know which weights are in it, the byte side was
computed exactly from the checkpoint's own tensor shapes; the total agrees with the index metadata
(198,042,331,512 bytes) exactly, so no tensor is missing from the inventory. Layer 45 (the MTP layer)
is excluded — see §8.

| # | Family | Layers | Params | BF16 GB (all ranks) | GB / rank | % of dense |
|---|---|---:|---:|---:|---:|---:|
| 1 | KDA `q_proj` + `k_proj` + `v_proj` | 34 | 3.423 G | 6.845 | 2.282 | **41.8 %** |
| 2 | KDA `o_proj` | 34 | 1.141 G | 2.282 | 0.761 | 13.9 % |
| 3 | MLA `o_proj` | 11 | 0.738 G | 1.476 | 0.492 | 9.0 % |
| 4 | Shared expert `gate` + `up` | 42 | 0.705 G | 1.409 | 0.470 | 8.6 % |
| 5 | `lm_head` | 1 | 0.634 G | 1.269 | 0.423 | 7.7 % |
| 6 | Shared expert `down` | 42 | 0.352 G | 0.705 | 0.235 | 4.3 % |
| 7 | Dense MLP layers 0–2 `gate` + `up` | 3 | 0.302 G | 0.604 | 0.201 | 3.7 % |
| 8 | MLA `q_b_proj` | 11 | 0.277 G | 0.554 | 0.185 | 3.4 % |
| 9 | MLA `kv_b_proj` | 11 | 0.185 G | 0.369 | 0.369 (replicated) | 2.3 % |
| 10 | Dense MLP layers 0–2 `down` | 3 | 0.151 G | 0.302 | 0.101 | 1.8 % |
| 11 | MLA `q_a` + `kv_a` (fused A) | 11 | 0.092 G | 0.184 | 0.184 (replicated) | 1.1 % |
| 12 | KDA `f_b` + `g_b` | 34 | 0.071 G | 0.143 | 0.048 | 0.9 % |
| 13 | DSA `indexer.wq_b` | 11 | 0.069 G | 0.138 | 0.046 | 0.8 % |
| 14 | KDA `b` + `f_a` + `g_a` | 34 | 0.045 G | 0.089 | 0.089 (replicated) | 0.5 % |
| 15 | DSA `indexer.wk` + `weights_proj` | 11 | 0.007 G | 0.014 | 0.014 | 0.1 % |
| | **Total streamed dense** | | **8.19 G** | **16.383** | **5.90** | **100 %** |
| — | `embed_tokens` (gathered, not streamed per token) | 1 | 0.634 G | 1.269 | 0.423 | excluded |

`fused_qkv_a_proj`, `indexer.wq_b` / `wk` and KDA `g_a_proj` are not split by tensor parallelism, so
their per-rank column equals the global one.

**A naming correction, measured:** this checkpoint has **no fused KDA `in_proj`**. It stores eight
separate tensors (`q_proj`, `k_proj`, `v_proj`, `b_proj`, `f_a_proj`, `f_b_proj`, `g_a_proj`,
`g_b_proj`) plus the conv1d, `A_log`, `dt_bias` and `o_norm`. The fusion happens **inside vLLM**,
which maps `in_proj_qkvgfab ← [q_proj, k_proj, v_proj, b_proj, f_a_proj]` into one
`MergedColumnParallelLinear`. That has consequences, in §8. Routed experts are likewise stored as
288 separate per-expert tensors, not a fused 3-D one.

### 7.1 A second pass over the same traces, and what it changed

Distributing the measured dense class over these byte shares is defensible in a bandwidth-bound
regime, but it is **modelling, not measurement**. A second pass went back to the traces and
attributed kernels by name, grid shape and calls-per-step. It moved the conclusion:
`[measured-here, raw not published]`, 2026-09-06.

- Of the ~51 ms of dense per C1 step, the part that is **serial and therefore recoverable** is
  **32.3 ms**. The rest is overlapped with other work or hidden behind kernels that would not get
  shorter.
- **68 % of that recoverable time is KDA**, and a single GEMM — the fused `in_proj_qkvgfab` — is
  **15.9 ms** of it. One module is roughly half of the entire recoverable dense budget.
- **The shared expert is hidden under marlin.** It is 12.9 % of dense bytes, but it does not sit on
  the recoverable path, so quantising it buys nothing measurable. That inverts the staging you would
  choose from the byte table alone: the cheap, low-risk families (`lm_head`, shared expert, dense
  MLP 0–2 — 26 % of the bytes) are close to **worthless**, and the whole reward sits in the
  attention families, which are exactly the ones with a structural obstacle (§8) and the higher
  quality risk.
- Two items are free of any quantization work and were left on the table when the line closed:
  a **cuBLAS kernel-selection issue in the KDA GEMM** worth roughly **+3 % at C1**, and the fp32
  `gemmSN_TN_kernel` in the MLA family (0.053 ms per forward pass, ~+0.3 %).

**The KDA GEMM does not run at the speed it can run at.** Standalone, on an idle node with the same
shapes, it sustains **214 GB/s**; in the engine it sustains **150 GB/s** — a 37 % gap, of which
alignment accounts for only 3–5 %. The suspicion is an operand-layout or heuristic difference inside
the engine's call, and it was **not** run down. `[measured-here, raw not published]`

---

## 8. Why the dense half was not quantized

It is the only large lever this profile leaves open, and it was **not taken**. Three reasons, in
descending order of importance.

**The owner's call: quality, not speed.** Reaching for another 25–35 % of single-stream throughput by
dropping this model's attention path to 4 bits is a quality risk taken for a speed goal, and speed is
not the only goal of this cluster. The decision, on 2026-09-06, was **no**. `[not tested]`

**Even done perfectly it does not change the outcome that mattered.** The honest projection, built
from two anchors measured on this hardware — marlin NVFP4 W4A16 takes **0.281×** the time of bf16 at
M = 8, which lands exactly on the theoretical byte ratio (0.5 + 0.0625)/2 = 0.281, and marlin already
attains 92–99 % of the DRAM ruler — is:

| Arm | C1 aggregate tok/s | C8 aggregate tok/s | KV pool (tokens) |
|---|---|---|---|
| Today (candidate) | 51.7 | 148.0 | 4,347,826 |
| Fully quantized dense `[estimate]` | 65–70 | ~163–168 | ~4.8 M |
| Our EXL3 stack, same model, same nodes `[measured-here]` | 70.5 | 194.0 | 5,619,834 |

Best case it reaches **parity at C1** and stays **~15 % behind at C8 and on the KV pool**, because
C8 is 62 % MoE and MoE is at the memory wall. For a workload of 6–8 concurrent agents — the two
numbers that decide it here are C8 aggregate and KV pool — the finish line is behind where the other
line already is. `[estimate]`

**And the biggest family is held hostage by a fusion.** vLLM merges five checkpoint tensors into one
`in_proj_qkvgfab` module, and `ModelOptMixedPrecisionConfig` requires **every shard of a fused module
to carry the same `quant_algo`**, raising `ValueError: Mixed quant_algo within fused layer` otherwise.
So quantising `q_proj`/`k_proj`/`v_proj` — 41.8 % of dense bytes, the single largest item, and the
module that holds 15.9 ms of the recoverable 32.3 — forces `b_proj` and `f_a_proj`, two KDA gating
arms, down to 4 bits with them. Those are precisely the tensors that the EXL3 checkpoint's author
left in bf16 **for sensitivity reasons, not shape reasons**, with the reason written into that
project's source. It is an all-or-nothing block on the only part of the model worth quantising.
`[measured-here]` for the mechanism (read from the image's own source), `[not tested]` for what
4-bit gating arms would do to the model.

**One cheap item was found beside that plan and never run.** Layer 45 (MTP) is loaded at every boot
and **never used**, because production speculates with a separate draft checkpoint
(`SPEC_METHOD=dflash`). It occupies about 7.84 GB, roughly 2.6 GiB per rank — not loading it is worth
about **+7 % KV pool** and is a one-line loader filter, a larger memory gain than the entire
quantization plan. Only the arithmetic was done; feasibility was never checked. `[not tested]`

---

## 9. What this document does not say

- **No per-family millisecond figure here is a direct measurement of that family** except where §7.1
  says so. The byte shares are exact; the millisecond split in the byte table is a distribution.
- **The draft model's share of dense was not separated out.** On the sibling line it was 4.60 ms; it
  was not isolated here, which is why §7.1 gives a recoverable figure rather than a full accounting.
- **What a quantized dense path does at prefill's M ≈ 2,041 is unknown.** On the sibling line the
  equivalent change made prefill dense **10.4 % slower**. marlin's dequantization is cheaper than
  that stack's, so we expect less — but an expectation is not a measurement, and the MoE bench's
  large-M rows belong to MoE geometry and do not transfer to dense.
- **The 37 % standalone-vs-in-engine gap in the KDA GEMM was not diagnosed**, only measured.
- **The head node's ACPI thermal zone peaks at 94–96 °C** (the other two at 76–89). No throttle was
  ever observed and clocks do not drop, so it is recorded as an observation only; which component it
  is, and what it means over years, was not investigated.
