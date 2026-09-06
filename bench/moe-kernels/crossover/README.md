# FP4 crossover sweep — where, if anywhere, an FP4 MoE path overtakes marlin

This is the adversarial re-measurement of [`../`](../) — the MoE kernel bench one directory up. That
bench measured three batch sizes (M = 8 / 64 / 1,792) and concluded that the production marlin W4A16
path has nothing to give up to the chip's FP4 tensor cores. The author of the `cuda-exl3` kernel
project disagreed: an sm_121-native NVFP4 path, he argued, would be much better both at prefill and
under concurrency, and the earlier bench was biased. This directory is the re-run built to prove him
right if he was.

It asks two questions the first bench could not answer:

1. **At which M does the best FP4 path first beat marlin?** Ten batch sizes instead of three, so a
   crossover can actually be located rather than assumed.
2. **What is the floor a perfectly fused custom FP4 kernel could not go below?** The FP4 path timed
   with its activation quantisation, row shuffle, routing metadata and epilogue combine all removed —
   the two grouped FP4 GEMMs alone.

Results: [`results/kernels/fp4-crossover-sweep-gb10.md`](../../../results/kernels/fp4-crossover-sweep-gb10.md).
Raw: [`results/kernels/gb10-crossover/`](../../../results/kernels/gb10-crossover/).

| File | What it is |
|---|---|
| `xsweep.py` | the sweep: `ruler`, `time` and `split` modes |
| `run-crossover.sh` | driver: smoke, rulers, four timing arms, the decomposition, the bf16 reference — one container each, with a host-memory watchdog |
| `rerun2.sh` | the two arms the watchdog killed at M = 4,096, re-run capped at M ≤ 1,792 |
| `agg-crossover.py` | turns the JSONs into the tables on the results page |
| `engine-check.sh` | read-only engine pre-flight, plus a one-request warm-up afterwards |

`xsweep.py` is a separate script from `../moe_kernel_bench.py`, not a patch of it. The differences
are deliberate and each one is a fix for something the first bench got wrong or could not see:
per-set build and prep (GPU peak about 3.5 GiB instead of 8.2 GiB), byte accounting taken from the
arm's actually-resident tensors instead of a formula, the b12x wrapper's `max_num_tokens` exposed as
a knob, and ten M values instead of three.

## Running it

Everything is inside the production image. No engine restart, no environment file, no checkpoint, no
model. One arm, one container, `--rm`.

```bash
docker run --rm --gpus all --ipc=host --cpuset-cpus 10-14 --memory=6g -v /var/tmp/xsweep:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/xsweep.py ruler --ruler-gib 2.0 --out /bench/out/ruler.json
```

```bash
docker run --rm --gpus all --ipc=host --cpuset-cpus 10-14 --memory=6g -v /var/tmp/xsweep:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/xsweep.py time --arm marlin-w4a16 --M 8 16 32 64 128 256 512 1024 1792 4096 --forms dense epcompact --routing uniform zipf --a2dq 0.2360684 --warmup 20 --iters 50 --rounds 3 --mem-floor 6 --out /bench/out/marlin.json
```

```bash
docker run --rm --gpus all --ipc=host --cpuset-cpus 10-14 --memory=6g -v /var/tmp/xsweep:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/xsweep.py split --M 8 16 32 64 128 256 512 1024 1792 --a2dq 0.2360684 --mem-floor 6 --out /bench/out/split.json
```

Or the whole thing, which is what produced the published files. Copy `xsweep.py` into the bench
directory the driver mounts first.

```bash
mkdir -p /var/tmp/xsweep/out /var/tmp/xsweep/logs && cp xsweep.py /var/tmp/xsweep/
```

```bash
./run-crossover.sh 2>&1 | tee /var/tmp/xsweep/logs/RUN.log
```

```bash
XSWEEP_OUT=/var/tmp/xsweep/out python3 agg-crossover.py > TABLES.md
```

`--memory=6g` is host RAM for the container and it is deliberately tight; see *Hygiene* below.
`--a2dq` is the post-SiLU dequantisation global scale, measured once by the first bench's correctness
gate (`a2_dq` in `gate-A.json`) and passed in so the cutlass arm's second FP4 quantisation is not fed
a wrong global scale. Re-measure it if you change `K`, `N` or the activation sigma.
`TORCH_EXTENSIONS_DIR` is set to a mounted directory because the `c_custom_stream_read` ruler
compiles a small CUDA extension at run time.

The correctness gate is not repeated here. Every arm in this sweep is an arm the first bench already
gated at these shapes; `run-crossover.sh` runs a two-minute API smoke pass first instead, which only
proves each arm still constructs and returns.

## The pre-registered design

The design, the thresholds and the verdict criteria below were written **before** the run, in the
source report, and were not changed afterwards. That is the point of the exercise: the objection
being tested is that the earlier verdict was reached by a biased measurement, and a verdict rule
written after seeing the numbers cannot answer that objection.

### The three rulers

The whole earlier conclusion rested on one sentence — *marlin is at 92–99 % of the memory ceiling,
so there is no idle arithmetic for FP4 to sell* — and that ceiling had been measured **one way**:
`torch.Tensor.sum()` over four rotating 32 MiB bf16 buffers, median 240.5 GB/s. A ruler that reads
5–10 % low would have opened exactly that much room for the theory. So the ceiling is rebuilt three
independent ways and **the best of them is used**:

| # | ruler | What it does |
|---|---|---|
| a | `torch.sum()`, 4 × 32 MiB bf16 | the earlier ruler, repeated verbatim |
| a2 | `torch.sum()`, one 2.0 GiB bf16 buffer | separates the *method* from the *buffer size* (L2 is 24 MiB) |
| b | `cudaMemcpyDeviceToDevice`, 2.0 GiB | read plus write; the read-equivalent figure is (2 × size) / time |
| c | custom `__ldg` / `uint4` streaming read, 2.0 GiB | hand-written, best over a block and thread sweep |

The LPDDR5X datasheet peak (273 GB/s) is recorded for context and is **not** used as the ceiling.

### The sweep

- **Layout A only** — the production shape: 96 local experts of 288, hidden K = 4,096, per-rank
  intermediate N = 2,048, top-8.
- **M ∈ {8, 16, 32, 64, 128, 256, 512, 1024, 1792, 4096}.**
- **Arms:** marlin W4A16 · vLLM cutlass W4A4 · FlashInfer b12x W4A4 · bf16 reference.
- **Forms:** `dense` (all-8-local: top-8 over the 96 local experts) and `epcompact` (EP-effective:
  top-8 over 288, non-local pairs dropped, survivors run as topk = 1). The second is the traffic an
  expert-parallel rank actually sees. The forms are defined and justified in
  [`../README.md`](../README.md).
- **Routing:** `uniform` and `zipf` s = 1. Verdicts are read from `uniform`; on `epcompact`, `zipf`
  is a rank-imbalance reading rather than a hot-expert reading, and is reported as such.
- **Protocol:** CUDA events, 20 warm-ups, 50 timed calls × 3 rounds, round median. Weights rotate
  over **two distinct banks** so the 24 MiB L2 cannot hold the working set; the smallest per-call
  expert-weight read is 0.26 GiB = 11 × L2. The bf16 arm uses one bank because two do not fit, and
  reads 2.4–4.8 GiB per call anyway.
- **Crossover M** = the first M at which the best FP4 path beats marlin by **more than 5 %**,
  reported separately per form and per routing.

### The GEMM-only bound

The unbiased part the objection asked for. The FP4 path is timed with the plumbing removed:
activations pre-quantised, routing metadata pre-built, no `shuffle_rows`, no epilogue combine. What
is left is the two grouped FP4 tensor-core GEMMs — a floor no fused kernel can go below, because it
must still stream the same weights and do the same MACs. `act-quant` alone and `metadata + combine`
alone are timed and reported separately, so the plumbing can be priced rather than argued about.

b12x has no pre-quantised entry point in its public API (`run()` takes bf16 `x` only), so the FP4
GEMM floor is taken from the cutlass grouped GEMM and labelled that way.

### The b12x wrapper A/B

The earlier bench always constructed `B12xMoEWrapper` with `max_num_tokens ≥ 2048`, even when timing
M = 8. That is faithful to production — `flashinfer_b12x_moe.py` builds the wrapper once from
`max_num_batched_tokens`, which is 2,048 here — so it is not an unfairness to correct. It is an
unmeasured question: what would per-batch sizing be worth? Both are run (`--b12x-maxtok matched` and
`fixed`) and the difference is published.

### The roofline

```text
W            = experts touched x measured bytes per expert
A            = (rows_in + rows_out) x K x 2        (bf16 in once, out once)
t_mem        = (W + A) / ceiling
FLOPs        = pairs x 6 x N x K                   (logical, dequantised math)
t_fp4        = FLOPs / 500e12                      (datasheet dense FP4 tensor peak, sm_121)
t_bf16       = FLOPs / 97.3e12                     (our measured BF16 tensor throughput)
roofline_fp4 = max(t_mem, t_fp4)
```

`marlin / roofline_fp4` is the honest ceiling at a point: the most any custom kernel could win there.

### The verdict criteria

At an operating point the theory is **SUPPORTED** if and only if:

- **A:** the GEMM-only FP4 path beats marlin by more than 10 % (`gemm-only / marlin < 0.90`), **or**
- **B:** `roofline_fp4` is more than 1.25× faster than measured marlin
  (`marlin / roofline_fp4 > 1.25`).

Otherwise **NOT SUPPORTED**. No softening, and the verdict is written out per point.

The operating points are the places our traffic actually sits: M = 8 (one stream verifying a 7-token
draft), 64 (8 streams), 128 (16), 256 (32) and 1,792 (a prefill chunk under
`--max-num-batched-tokens 2048`). M = 4,096 is not an operating point — one forward pass never sees
it — and is measured only to show the shape of the curve.

## Hygiene

These rules were fixed before the run, because the first bench cost more than it should have: it took
16 GiB of host RAM on a node with 11.7 GiB available, drove `MemAvailable` to 1.0 GiB and pushed a
resident idle engine about 3.4 GiB into swap.

- Every container is `--rm --memory=6g --cpuset-cpus 10-14`, one arm at a time.
- `MemAvailable ≥ 12 GiB` before starting. A host-side watchdog samples every 5 s and, after two
  consecutive readings below 6 GiB, kills the container by name. `xsweep.py` carries the same floor
  internally (`--mem-floor`) and aborts itself.
- Every timed point is flushed to the output JSON as it completes, so a watchdog kill at a later,
  larger M does not throw away everything measured before it.
- Weight sets are built and prepped one at a time, which is what brings the GPU peak from 8.2 GiB
  down to about 3.5 GiB.
- If an engine is serving on the node, `engine-check.sh status` confirms it is idle
  (`num_requests_running` and `num_requests_waiting` both 0) before the sweep, and
  `engine-check.sh warmup` sends one short request afterwards so the next real request does not pay
  a page-in. Engine containers and systemd units are never touched. The script is read-only apart
  from that one request; point it at your own node with `HEAD_HOST` and `HEAD_PORT`.
- Take whatever cluster-wide measurement lock you use, and release it on exit including on failure.
  Never delete someone else's.

## Known limits

- **Single GPU, single process.** Everything here is inside one MoE kernel call. What share of a
  real decode or prefill step the MoE occupies is a different measurement and is not here.
- **No expert-parallel communication.** The `epcompact` form reproduces the *traffic* an EP rank
  sees, not the all-to-all around it.
- **No correctness gate in this sweep.** See above; the arms were gated by the first bench.
- **Synthetic weights and synthetic routing.** Gaussian σ = 0.25, quantised expert by expert; byte
  count and shape are the real ones, so the speed figures stand, and nothing here is a statement
  about the real checkpoint's numerics.
- **Three rounds.** Round-to-round spread came out at median 0.52 %, max 4.17 %, and all three rounds
  are in every JSON — but three is three. Differences under 5 % should be read as near-noise, which
  is why the crossover threshold is 5 %.
- **`agg-crossover.py` reads `XSWEEP_OUT`.** It expects the seven result JSONs by name
  (`ruler`, `marlin`, `cutlass`, `b12x-matched`, `b12x-fixed2k`, `split`, `bf16`) and silently omits
  any table whose input is missing.

## What it costs

Fifteen minutes of one GPU (05:12 to 05:27 Istanbul), plus the two re-runs. GPU peak 6.94 GiB
(the marlin repack). Host `MemAvailable` never went below 6.26 GiB and nothing went into swap. The
engine on that node stayed up and idle throughout and answered `/health` 200 before and after; it was
sent one warm-up request at the end.

The cost that was paid: the sweep waited **2 hours 2 minutes** for the cluster measurement lock, and
the watchdog killed the `split` and `bf16` arms at M = 4,096 (`rc=137` in
[`RUN.log`](../../../results/kernels/gb10-crossover/RUN.log)). They were re-run capped at M ≤ 1,792
by `rerun2.sh`. M = 4,096 is above the production `max-num-batched-tokens`, so no verdict depends on
those two points.
