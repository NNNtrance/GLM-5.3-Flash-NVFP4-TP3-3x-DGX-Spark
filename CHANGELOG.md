# Changelog

Dated entries for this repository. It starts on 2026-09-06; everything before that date is the
initial recipe, and its history lives where it belongs — the branches we opened and closed in
[08 — What we tried and rejected](docs/08-what-we-tried.md), the items still open and the claims we
withdrew in [09 — Open problems](docs/09-open-problems.md).

This is a working record, not a release history.

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
