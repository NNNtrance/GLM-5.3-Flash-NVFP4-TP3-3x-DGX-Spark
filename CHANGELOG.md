# Changelog

Dated entries for this repository. It starts on 2026-09-06; everything before that date is the
initial recipe, and its history lives where it belongs — the branches we opened and closed in
[08 — What we tried and rejected](docs/08-what-we-tried.md), the items still open and the claims we
withdrew in [09 — Open problems](docs/09-open-problems.md).

This is a working record, not a release history.

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
