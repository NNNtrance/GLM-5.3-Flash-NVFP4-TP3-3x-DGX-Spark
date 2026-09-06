# Changelog

Dated entries for this repository. It starts on 2026-09-06; everything before that date is the
initial recipe, and its history lives where it belongs — the branches we opened and closed in
[08 — What we tried and rejected](docs/08-what-we-tried.md), the items still open and the claims we
withdrew in [09 — Open problems](docs/09-open-problems.md).

This is a working record, not a release history.

---

## 2026-09-06 — closing state: the production candidate, the measured profile, and what we closed

**Status change, stated at the top of the [README](README.md).** This repository is a complete,
working recipe and stays up as one, but it is **not under active development**: the maintainers' main
line has moved to the EXL3 recipe for the same model on the same hardware,
[`NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark`](https://github.com/NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark).
Issues and pull requests are still read and welcome. Four documents were added to write down where
the line was put down, so that nobody has to re-run what we already ran.

**New: [`docs/10-production-candidate-and-lessons.md`](docs/10-production-candidate-and-lessons.md).**
Five findings from the EXL3 line that look format-independent were carried into a separate tree on
the three nodes, applied one at a time, and measured against the untouched production tree as
control. The candidate that came out of it is the mesh-plugin patched build (patches 0004/0005/0006,
dual-cable link selection and `NCCL_PTR_CUDA`) + the current upstream chat template + a settle gate,
at `gpu-memory-utilization` 0.88 which was never touched: **C1 51.7 / C8 148.0 aggregate, per-stream
56.6 / 22.5, TTFT 0.384 / 1.046, prefill 7k 1,699 and prefill-fresh 1,938, KV 4,347,826 tokens with
rank-consistent equivalent fractions, acceptance 62.2 %, gates 10/10 · 12/12 cold and warm, tool-call
8/8, MMLU sample 86.52 ± 0.74, needle-lite 6/6, boot 234 s.** `[measured-here, raw not published]`

**On decode this candidate is the same engine.** Every decode difference against the shipped
production configuration is inside the noise band. What it buys is **prefill +4.8 % (7k) and +6.4 %
(fresh)** and a tool-calling correctness fix the gate can see. The template — upstream commit
`690b7052`, sha256 `0c4099f3…`, 10,950 bytes, obtainable from the checkpoint repository you already
downloaded — fixes a literal `None` leaking into the prompt on assistant turns carrying a tool call,
and it is free: speed, KV, acceptance and prefill all equal. `posix_fadvise` + `malloc_trim`, worth
+4.1 % KV on the sibling line, is worth **+0.2 %** here, because `instanttensor` leaves no allocator
arena to reclaim (60 MB against 2.7 GiB).

**One verdict retracted in the same session it was made.** `NCCL_MAX_NCHANNELS=8` read as a no-op in
its single arm and was about to be dropped. A pooled comparison and then a same-session
single-variable A/B say it is worth **+7.7 % at C4 and +9.7 % at C6** — five concurrency levels out
of five moving the same way, C6 outside the band in both readings. The setting stays. The lesson is
about method: a one-arm reading at C1 and C8 missed an effect that lives in the middle.

**One measurement instrument was lying about a whole column.** The `MemFree` and `swap` columns of
the first five arms were truncated to integers by a locale mismatch in the sampler, so "0.00 GiB
swap" actually meant "less than 1 GiB". The sampler now runs under `LC_ALL=C` and also records
`MemAvailable` and `Cached`. Also corrected: the sweep runner was queueing `prompts[i % 12]`, so C1
and C2 saw only 8 of the 12 prompts while C8 saw all twelve — the concurrency levels were being
compared on different work.

**And the KV pool number turns out to depend on an estimator.** vLLM's CUDA-graph memory profiler
reserves **~3.9 GiB** against the budget while the graph pool actually allocated is 0.0–0.5 GiB, and
the estimate swings ~2× from boot to boot; the pool is sized from the worst rank's estimate. A KV
column therefore cannot be read without the per-rank `equivalent to --gpu-memory-utilization` line
beside it, and a KV difference may not be attributed to a variable unless those lines match. That
rule is now in our harness, and it is why this entry does **not** claim a KV gain.

**New: [`docs/11-measured-profile.md`](docs/11-measured-profile.md).** Kernel-level breakdowns of one
prefill chunk and one decode step at C1 and C8, on all three ranks, with the profiler's own cost
measured in the same boot (+1.4 % prefill, +2.3 % C1, +2.5 % C8 — a seventh of what the same
measurement costs on our EXL3 stack). Shares were computed two independent ways and agreed. The three
numbers it exists for: **BF16 dense linears are 49.4 % of a C1 step, 17.5 % of a C8 step and 19.1 %
of a prefill chunk; MoE marlin is 38.4 / 62.4 / 30.6 %; NCCL is 13.8–15.1 / 9.2 / 11.9–13.9 %.**
Spread across ranks is ≤ 0.8 points in every class. The prefill chunk is **2,041 tokens against a
2,048 budget — 99.7 %**, which narrows [09 item 8](docs/09-open-problems.md). Also in there: the exact
BF16 weight inventory by family (the total agrees with the checkpoint's index metadata to the byte),
and a second trace pass that changed the conclusion — the recoverable serial dense time is **32.3 ms
of the ~51, 68 % of it KDA**, one fused module is 15.9 ms of it, and the shared expert is hidden
under marlin so quantising it would buy nothing. Two things were left on the table when the line
closed: a cuBLAS kernel-selection issue in the KDA GEMM worth ~+3 % at C1, and the fact that the same
GEMM sustains 214 GB/s standalone against 150 GB/s inside the engine.

**New: [`docs/12-what-we-closed.md`](docs/12-what-we-closed.md).** Twelve levers, each with the number
that closes it. `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS=0` works exactly as advertised — **+9.3 %
KV pool**, and the accounting shows the reservation moving out of the "peak activation" term — and is
**rejected** on two independent grounds: **3.54 GiB of swap on rank 0** and **C8 −3.8 %, outside the
band**, plus a code-gate failure that repeated on the same item, plus a 12 % longer boot.
**DVFS is not a bottleneck:** the throttle flag never lit in **1,478 samples**, `-lgc 3003,3003` is
accepted on all three nodes and changes the actual clock by +0.3 / +0.5 / −0.04 %, and an independent
published measurement on the same part agrees. **CPU idle states do nothing:** an apparent +4.8 % was
destroyed by the return leg — the number did not come back down — and turns out to be a ~30-minute
post-boot upward drift in C1, now a measurement rule of its own. Launch overhead is closed at
98.1–99.2 % occupancy; the memory fraction above 0.88 is closed in this configuration; the fp32
strided-batched MLA GEMM is worth about +0.3 % and is recorded so nobody spends a day on it.

**The one open lever, and why it was not taken.** Quantising the BF16 dense path is the largest item
the profile exposes, and the same change was worth **+23 %** single stream on our EXL3 stack. It was
**not done, on the owner's quality call** — a 4-bit attention path is a quality risk taken for a
speed goal. The supporting arithmetic, published so the decision can be argued with: anchored on
marlin NVFP4 W4A16 taking **0.281×** the time of BF16 at M = 8 (landing exactly on the theoretical
byte ratio) and on marlin already attaining 92–99 % of the DRAM ruler, a fully quantized dense path
reaches roughly **C1 65–70, C8 163–168, KV ~4.8 M** against **70.5 / 194.0 / 5,619,834** measured on
the EXL3 stack on the same nodes — parity at single stream, ~15 % behind where it counts, because C8
is 62 % MoE and MoE is at the memory wall. There is also a structural block: vLLM fuses five
checkpoint tensors into one module and requires one `quant_algo` across it, so quantising q/k/v —
41.8 % of dense bytes — forces two KDA gating arms to 4 bits with them. `[estimate]` for the
projection, `[not tested]` for the quality.

**New: [`docs/13-checkpoints.md`](docs/13-checkpoints.md).** Five public NVFP4 quantizations of the
same base model compared on producer, scope, activation quantization, size, freshness, licence and
published evidence — and one defect measured directly, from **safetensors headers and range-read F32
scalars, about 15 MB in total**, with no download and no GPU. The gate/up global-scale mismatch that
vLLM's fused MoE turns into a silent numerical error measures **0.0 % at four depths** for
`RedHatAI`, `RadixArk` and our production checkpoint, and **64.7–83.3 %** (worst pair ratio 2.60) for
`LibertAIDAI`, against 68.5 % previously measured for `orcarouter`. **`[retracted]`: "the gate/up
mismatch is a property of the model, not of the quantization tool."** It is a property of the tool;
the original verdict came from two checkpoints that were two samples of the same class. Two
consequences: changing producer is a correctness fix, and a workaround patch we had been carrying is
unnecessary on the production checkpoint. The production checkpoint stays, for three reasons in this
order — measured-clean gate/up scales, the only calibration corpus in the field that matches agentic
coding traffic (structural, `[not tested]`), and a BF16-only TP=3 pad path that one of the five
candidates would require new code for.

**Also updated:** the [README](README.md) status block and a *Closing state* section in its document
index; [09 — Open problems](docs/09-open-problems.md), where item 2 (one node permanently slower) is
marked **closed as to remedy** — the per-node clock plateaus reproduce and a clock lock changes
nothing — and item 8 (`--max-num-batched-tokens`) is marked **narrowed**.

**What it cost:** six engine boots and about seven hours of cluster time across two sessions, under
one measurement lock held end to end. No checkpoint, sidecar or image was deleted;
`gpu-memory-utilization` was never changed; the production tree was never written to, because it was
the control arm. The only system settings touched were CPU idle states, changed for one A/B and
restored file by file, and a GPU clock lock, released and confirmed released. The honest ledger of
the transfer experiment: of five lessons carried over, one was a clean free gain, one was a prefill
gain, one was nearly discarded on a bad reading and is worth +8–10 % in the middle of the concurrency
range, one did essentially nothing, and one cannot be cleanly attributed. Two verdicts were corrected
and one instrument was found to be lying.

---

## 2026-09-06 — the FP4 crossover sweep: the crossover is real, and it is above our traffic

**New: [`results/kernels/fp4-crossover-sweep-gb10.md`](results/kernels/fp4-crossover-sweep-gb10.md)
and [`bench/moe-kernels/crossover/`](bench/moe-kernels/crossover/).** The author of the `cuda-exl3`
kernel project did not accept the MoE kernel study below — b12x is not perfect for sm121, he argued,
and an sm_121-native NVFP4 path would be much better both at prefill and under concurrency, so set
the earlier bias aside and measure again. This is that re-measurement, built wanting the theory to
come out true: **the measurement design, the thresholds and the verdict rule were written before the
run** and are published unchanged, because a verdict rule written after seeing the numbers cannot
answer a charge of bias. Same conditions as the first study — one GPU, no engine, no checkpoint, no
model, the production image's own kernels on synthetic NVFP4 expert banks in the checkpoint layout.
`[measured-here]`

**The earlier verdict survives, on much harder evidence.** The memory ceiling was rebuilt three
independent ways — `torch.sum()`, `cudaMemcpyD2D` and a hand-written `__ldg`/`uint4` streaming reader
— and they agree inside 3 % at **238.6 / 245.9 / 239.5 GB/s**; the single-method 240.5 GB/s ruler the
first study leaned on is **2.2 %** below the best of them, so that objection failed. Against a
245.9 GB/s ceiling, marlin sits at **91–96 %** at every decode and concurrency point, and the decisive
new number is that **even a zero-overhead FP4 GEMM is 1.03–1.07× slower there**: the FP4 path was
timed with pre-quantised activations, pre-built routing metadata, no row shuffle and no epilogue
combine — the floor no fused kernel can go below — and it still loses. A custom sm_121 path could win
at most **4–9 %** at M = 8 / 64 / 128 / 256, and the measured reality today is minus 3–7 %.

**The crossover was located, and it sits above the traffic this recipe runs.** Ten batch sizes instead
of three. With all eight experts local the best FP4 path first beats marlin by more than 5 % at
**M = 1,024** (uniform and Zipf); in the EP-effective form every rank actually sees, at **M = 4,096**
under balanced routing and **M = 1,792** under the worst rank imbalance. Production runs
`max-num-batched-tokens 2048`, so a single forward pass never reaches the balanced-routing crossover.
Pre-registered verdicts: **NOT SUPPORTED** at M = 8, 64, 128 and 256 in both forms; **SUPPORTED** at
M = 1,792 all-8-local (GEMM-only 0.56×, roofline 2.33×) and in the EP form only under the Zipf
pathology (0.64×) — and **NOT SUPPORTED** at M = 1,792 in the production EP form with balanced
routing (0.98×).

**Two flaws in the study below, found and fixed.** *Coverage:* nothing had been measured between
M = 64 and M = 1,792, and that gap hides a **marlin cliff at M = 512 → 1,024** in the all-local form
(6,619 → 10,355 us, +56 %, round spread 0.2–1.9 %, so not noise) that the FP4 GEMM does not have.
That cliff is where the whole all-local FP4 advantage comes from — a marlin scheduling artefact, not
an FP4 tensor-core win. *Plumbing charged to the kernel:* at M = 1,792 all-local, **38 %** of the FP4
path's wall time is outside the GEMM (2,052 us act-quant + 2,673 us metadata/combine of 12,461 us).
Stripped bare the FP4 GEMM is **level with marlin** in the production form at prefill (0.98×) rather
than 29 % behind. That correction is owed to the author and is recorded as one — but level is not
ahead, and at M ≤ 512 the same plumbing costs only 142–148 us, about 3 % of the call.

**Two hypotheses raised against the first study and disproved.** Marlin does **not** inflate its
NVFP4 scales to bf16 — byte accounting taken from the arms' resident tensors gives all three
quantised arms **14,155,78x B per expert**, 1.0000× the 4-bit + fp8-scale model — so the first
study's GB/s tables were right. And the first bench did **not** bypass b12x's wrapper: production
constructs the same `B12xMoEWrapper`. One sentence in the study below is weakened by the second check
and is flagged in place: b12x's measured `nvidia.gb10.48sm` profile is a **vendor signal**, not a
statement about the code we run, because vLLM's `flashinfer_b12x` backend does not go through that
policy layer.

**Two concrete things handed to the kernel author.** On identical byte counts the FP4 grouped GEMM
sustains **211–220 GB/s** where marlin's complete W4A16 path sustains **228–236 GB/s** — 89 % against
95 % of the ceiling — and closing that ~7 % weight-streaming gap is worth more at our operating points
than any tensor-core work. And sizing the SM12x MoE wrapper per batch instead of once from
`max_num_batched_tokens` is worth **8.8 % at M = 128 and 9.6 % at M = 256** in the all-local form
(nothing measurable in the EP form). The last part of the results page is written to stand alone for
that audience.

**Also updated:** a banner at the top of
[`results/kernels/moe-kernel-bench-gb10.md`](results/kernels/moe-kernel-bench-gb10.md) — superseded on
coverage, not on numbers; none of its figures changed — a *Side studies* entry in the README, and the
two new rows in [`results/README.md`](results/README.md).

**What it cost:** fifteen minutes of one GPU, 6.94 GiB peak GPU allocation, no engine restart and no
configuration change. The first study's host cost was **not** repeated: building one weight set at a
time held the GPU peak near 3.5 GiB, host `MemAvailable` never went below 6.26 GiB in an accepted arm
and nothing went into swap; the resident engine stayed idle and healthy and was sent a warm-up request
at the end. What it did cost: a **2 hour 2 minute** wait for the cluster measurement lock, and the
run's own memory watchdog killing the `split` and `bf16` arms at M = 4,096 — re-run capped at
M ≤ 1,792, which is above the production `max-num-batched-tokens` and affects no verdict. Zero
throttle events in 153 samples; round-to-round spread median 0.52 %, max 4.17 %.

---

## 2026-09-06 — the MoE kernel study: marlin loses nothing, and the no-EP layout is closed

**New: [`results/kernels/moe-kernel-bench-gb10.md`](results/kernels/moe-kernel-bench-gb10.md) and
[`bench/moe-kernels/`](bench/moe-kernels/).** A model-free MoE kernel bench: no engine, no
checkpoint, no model. Synthetic NVFP4 expert banks are built in the *checkpoint* layout with the
production image's own `scaled_fp4_quant`, handed to each backend's own repack helper, and the MoE
kernels are called directly on one GPU. Four arms — marlin W4A16 (the production path), vLLM cutlass
W4A4, FlashInfer b12x W4A4, and a bf16 reference — at both the production shape (TP=3 + EP: 96 local
experts of 288, K = 4,096, N = 2,048) and the no-EP alternative (288 local experts, intermediate
2,304 sliced by three, N = 768), at M = 8 / 64 / 1,792 under uniform and Zipf routing. The engine was
not started, not stopped and not touched. `[measured-here]`

**The headline is that our weight-only fallback is not a fallback.** At M = 8 and M = 64 the MoE
kernel already runs at **94–99 % of measured DRAM bandwidth** (225–237 GB/s against a 240.5 GB/s
ruler, with the bench's own byte-ratio self-check landing on 0.281 against a theoretical 0.28125). At
our own shape and traffic the best FP4 tensor-core path on this chip is **7 % slower at M = 8, 7 %
at M = 64 and 5 % at M = 1,792** than marlin — there is no idle arithmetic for FP4 to sell, and the
W4A4 activation-quantisation kernels give back 4–13 % of the band to produce something the memory
system cannot deliver faster. FP4 wins in exactly one regime: prefill-sized batches with all experts local,
where b12x is **1.42×** faster at M = 1,792 (76.1 TFLOPS against marlin's 53.7). Three ranks with
expert parallelism do not run that form.

**The no-EP TP-2304 arm is closed.** At equal token traffic and balanced routing it is
**1.16–1.26× slower** at every batch size and wants **+12.52 %** routed-expert weight bytes per rank
(1.424 against 1.266 GiB per weight set), which comes straight out of the KV pool. The FP4 advantage
disappears there too: 288 expert groups at N = 768 destroys the "many rows per expert" regime those
kernels want. Recorded as [08 item 35](docs/08-what-we-tried.md); its communication side is on the
other side of the ledger and is explicitly **unmeasured** — this bench is single-GPU.

**[09 item 7](docs/09-open-problems.md) is half closed, and the half that closed went the other
way.** It said the only route to measuring what marlin's dropped activation scales cost was the
corrupt b12x MoE + EP branch. That was wrong — the kernels can be called directly — and the answer
is that restoring those scales would **cost** speed, not buy it. The quality half stands untouched: at
the MoE layer W4A4 is about **29× noisier** than W4A16 on synthetic Gaussian activations (rel-L2
0.157 against 0.0054), which is a direction and not a verdict, and MMLU, the code exam and the
correctness probe have never been run on a W4A4 MoE. `[not tested]`

**[09 item 5](docs/09-open-problems.md) is narrowed.** The b12x FP4 MoE kernel now has a GPU-side
numerical comparison against a dequantised reference and an emulated W4A4 reference at the exact
production shapes, and it lands in the same error class as the reference (rel-L2 0.163 against 0.157)
— nothing like the 0.1–0.7 cosine band a corruption produces. The b12x FP4 arithmetic is not what
broke `t4b`. What the bench cannot exercise is the only remaining suspect: the FP4 paths refuse
`expert_map` outright, so the expert-parallel routing around the kernel is still untested.

**Three side findings, recorded because each one closes a door someone would otherwise try.**
There is **no marlin W4A8 door** — the image's `marlin_utils_fp4.py` refuses NVFP4 weights with a
1-byte activation dtype in five places, including line 363 in the MoE prepare path. **b12x is not
guessing on this GPU**: the image ships a measured `nvidia.gb10.48sm` profile targeting exactly
cc 12.1 / 48 SMs, and its `moe.ep_moe` component is a single leaf reading `"backend": "w4a16"` —
under expert parallelism the vendor's own measured profile picks the same class as marlin. And
**generating our own profile is not possible in this image**: `generate_gpu_profile.py` fails on
`--list-components` with `ModuleNotFoundError: No module named 'benchmarks.benchmark_qsa'`.

**Also added:** a new [08 — What we tried](docs/08-what-we-tried.md) section, *MoE kernel branches*,
with items 35 and 36; a *Side studies* entry in the README; and the note in
[`results/README.md`](results/README.md) that `results/kernels/` is a single-GPU side study rather
than a serving run.

**What it cost:** under seven minutes of one GPU for the timing sweep, about half an hour for the
whole session, 9.2 GiB peak GPU allocation, no engine restart and no configuration change. On the
host it was not free: the container took 16 GiB of RAM on a node with 11.7 GiB available, host
`MemAvailable` bottomed out at 1.0 GiB and a resident idle engine went about 3.4 GiB into swap. It
stayed healthy — zero requests running, `/health` 200 before and after — but its next request would
have paid the page-in.
