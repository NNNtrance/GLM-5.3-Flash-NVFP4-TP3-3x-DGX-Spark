# MoE kernel bench — model-free, one GPU, engine untouched

This directory answers one question at the kernel level: **on GB10 (sm_121), at the MoE shapes this
recipe actually runs, what does the weight-only marlin W4A16 path cost against the FP4 tensor-core
paths the chip has?** It does that without the engine, without the checkpoint and without a model:
synthetic NVFP4 expert banks are built in the *checkpoint* layout with the image's own
`scaled_fp4_quant`, handed to each backend's own repack helper, and the MoE kernels are then called
directly.

Our results are in [`results/kernels/moe-kernel-bench-gb10.md`](../../results/kernels/moe-kernel-bench-gb10.md);
the raw JSONs and logs are in [`results/kernels/gb10-moebench/`](../../results/kernels/gb10-moebench/).

| File | What it is |
|---|---|
| `moe_kernel_bench.py` | the bench: `probe`, `gate` and `time` modes |
| `run-all.sh` | driver: probe, both gates, then nine timing runs, one container each |
| `make-tables.py` | turns the JSONs into the markdown tables T1–T10 |

## Running it

Everything the bench needs is inside the production image. Nothing outside the container is touched:
no engine restart, no environment file, no checkpoint. One arm, one container.

```bash
docker run --rm --gpus all --ipc=host --memory=16g -v $PWD:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/moe_kernel_bench.py probe
```

```bash
docker run --rm --gpus all --ipc=host --memory=16g -v $PWD:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/moe_kernel_bench.py gate --layout A --M 64 --gate-experts 24 --arms bf16 marlin-w4a16 vllm-cutlass-w4a4 fi-b12x-w4a4
```

```bash
docker run --rm --gpus all --ipc=host --memory=16g -v $PWD:/bench --entrypoint python3 harem/glm53-lil:t10 /bench/moe_kernel_bench.py time --layout A --arm marlin-w4a16 --sets 2 --forms dense epcompact nativeep --M 8 64 1792 --routing uniform zipf --a2dq 0.2360684 --warmup 20 --iters 100 --rounds 3 --out /bench/out/A-marlin-w4a16.json
```

Or all of it at once, which is what produced the published files:

```bash
BENCH_DIR=/var/tmp/moebench IMAGE=harem/glm53-lil:t10 ./run-all.sh
```

```bash
python3 make-tables.py /var/tmp/moebench/out > TABLES.md
```

`--memory=16g` is host RAM for the container, and it is not generous: the marlin repack peaks at
8.2 GiB (layout A) and 9.2 GiB (layout B) of **GPU** memory, and the bf16 arm needs 4.5–5.1 GiB
resident. Our node had another engine resident and idle at the time, and the run pushed it into swap
— see "What it costs" below. `--cpuset-cpus` is optional; we pinned five cores so the sampler and
the container did not contend.

`--a2dq` is the post-SiLU dequantisation global scale. It is measured once by the gate
(`a2_dq` in `gate-A.json`) and passed to the timing runs so the cutlass arm's second FP4 quantisation
is not fed a wrong global scale. Re-measure it if you change `K`, `N` or the activation sigma.

## Shapes

`K` is the hidden size, `N` the routed-expert intermediate width per rank. Gate/up is one fused
tensor of `2 × N` rows; down is `K × N`.

| Layout | What it is | local experts | K | N | gate/up | down | top-k | expert map |
|---|---|---:|---:|---:|---|---|---:|---|
| **A** | production today: TP=3 + expert parallelism, 96 local of 288 | 96 | 4,096 | 2,048 | K=4096, N=2×2048 | K=2048, N=4096 | 8 of 288 | yes |
| **B** | the no-EP candidate: intermediate padded 2,048 → 2,304, sliced by 3 | 288 | 4,096 | 768 | K=4096, N=2×768 | K=768, N=4096 | 8 of 288 | no |

Layout B exists because of the constraint in
[docs/03](../../docs/03-launch-and-flags.md#31-tensor-parallel-3--expert-parallel): 2,048 does not
divide by 3, so without expert parallelism the intermediate has to be padded to a multiple of 3 and
sliced. 2,304 is that padding. Every rank then holds all 288 experts at one third of the width.

## Forms — how expert parallelism is modelled

| Form | What it is | Who can run it |
|---|---|---|
| `dense` | "all 8 local": top-8 over the 96 local experts, M rows | every arm |
| `epcompact` | the EP-effective traffic: route top-8 over 288, drop the non-local pairs, run the surviving (token, expert) pairs as topk=1 over 96 → about M × 8/3 rows | every arm |
| `nativeep` | what production runs: global ids over 288 plus an `expert_map` with −1 for non-local | marlin and bf16 only |

The FP4 paths refuse expert maps, so `epcompact` is the only way to put them on production traffic.
It is a fair proxy because `nativeep` and `epcompact` were **measured** equivalent on marlin —
within 1 % at M=8 and M=64, 6 % at M=1,792 (table T3 in the results page). Layout B has no EP, so it
is `dense` only.

## Routing

`--routing uniform zipf`. Both draw `top-k` **distinct** experts per row with
`torch.multinomial(replacement=False)`.

- `uniform` — every expert equally likely. This is the balanced reading.
- `zipf` — weight `1/rank` over expert **ids**, s = 1.

Read `zipf` carefully on layout A `epcompact`: expert parallelism gives rank 0 the ids 0–95, which is
exactly where the Zipf mass sits, so those rows are the **worst rank imbalance**, not a hot-expert
reading (11,457 of 14,336 pairs = 80 % on one rank against 33 % in balance). On `dense` and on layout
B every expert is local, so there `zipf` is a clean hot-expert reading.

## The L2 bank rule

GB10 has a 24 MiB L2. A bench that times one weight repeatedly leaves it resident and measures a
cache, not the memory system — which is the whole point of a weight-bandwidth-bound kernel. So:

- **two weight sets are built and alternated call by call** (`--sets 2`), and
- **the smallest amount of expert weight actually read per call is 0.188 GiB = 8 × L2**, with the
  typical call at 0.6–4.8 GiB.

The bf16 arm runs `--sets 1` because two sets would need 9.7–10.9 GiB and the node did not have it.
That arm reads 2.4–4.8 GiB per call — 100–200 × L2 — so set rotation changes nothing there.

## The ruler

Every `time` and `probe` run measures a plain bf16 read first (512 × 32,768, four buffers rotating,
three rounds) and writes it into its own JSON. It is there so the absolute GB/s figures can be
checked against a known-good number, and so a throttled or contended run announces itself. Across
the nine bench processes ours read min 220.3 / median 245.3 / max 249.5 GB/s.

The driver also samples SM clock, maximum clock, temperature, power and
`clocks_event_reasons.active` every five seconds into `logs/telemetry.csv`, so that a difference
between arms can be separated from a DVFS artefact.

## The correctness gate, and its emulated-W4A4 reference

`gate` runs every arm on identical inputs at the full production `K`/`N`/top-k with a reduced expert
bank (`--gate-experts 24`; the expert count does not change the numerics of a single expert's GEMM
pair, it only fits in memory). It builds **two** references from one dequantised weight bank:

1. **bf16 of the dequantised weights** — what a W4A16 arm should compute.
2. **emulated W4A4** — the same torch reference with the activations NVFP4 quantise-dequantised
   (`ref_nvfp4_quant_dequant`) at **both** GEMM inputs. This is what a W4A4 kernel should compute.

This second reference is the part that matters, and it is why the obvious gate is wrong. **No W4A4
path can reach cosine 0.99 against bf16 on this data — the emulated W4A4 reference itself only
reaches 0.9876.** Synthetic Gaussian activations are close to the worst case for FP4 activation
quantisation. So W4A16 arms are gated against reference 1 and W4A4 arms against reference 2.

The script's mechanical `VERDICT` field applies a flat cosine ≥ 0.99 against the arm's own reference
and therefore flags the b12x arm `NOT COMPARABLE` at 0.9856/0.9851. The results page explains why we
read that arm as comparable anyway and what the difference is; **if you run this, expect that flag
and read both numbers, not the verdict**.

`gate` also carries `marlin-w4a8fp8`, which exists only so its refusal is reproducible.

## Known limits

- **Single GPU, single process.** Everything here is inside one MoE kernel call. The share of a real
  decode or prefill step that the MoE occupies is a different measurement and is not in this bench.
- **No expert-parallel communication.** Layout B's advantage — no all-to-all dispatch/combine, one
  all-reduce instead — cannot be seen here at all. What can be seen is its kernel-side cost and its
  weight-byte cost.
- **Synthetic routing.** `uniform` and `zipf(s=1)` over expert ids; the real router's token-expert
  distribution was not measured. The marlin ↔ FP4 ratio came out nearly identical under both, which
  is evidence that the verdict is not sensitive to the distribution, not proof.
- **Synthetic weights.** Gaussian, σ = 0.25, quantised expert by expert. Byte count and shape are
  the real ones, so the **speed** figures stand; the numerical figures are not a statement about the
  real checkpoint.
- **W4A4 quality was not measured on the model.** The gate measures the numerical distance at the
  layer on synthetic data. What W4A4 does to MMLU or the code exam is a separate gate that has not
  been run.
- **Eager only.** No CUDA graphs, no launch-count profiling. All arms pay the same launch cost, so
  the ratios are comparable; the absolute microseconds include per-call launch overhead.
- **Three rounds.** Median round-to-round spread ≤ 1.1 %, all three rounds are in every JSON, but
  three is three. Differences under 5 % should be read as near-noise.
- **The b12x arm can hard-fault.** On its first attempt the `epcompact` form failed — first with a
  `topk_ids.shape[0]` mismatch, then with `cudaErrorInvalidAddressSpace`, which killed the process
  and would have taken any following arm with it. That log is published unedited as
  [`A-fi-b12x-w4a4.log`](../../results/kernels/gb10-moebench/A-fi-b12x-w4a4.log). Running the form
  in its own invocation, so that the `B12xMoEWrapper` is constructed for that `top_k` and no other,
  produced the published numbers. This is why `run-all.sh` gives every arm its own container and the
  b12x `epcompact` form its own run.

## What it costs

The timing sweep is **under seven minutes** of one GPU (02:04:11 → 02:10:59 in
[`RUNALL.log`](../../results/kernels/gb10-moebench/RUNALL.log)); the whole session — probe, both
gates, the sweep and the b12x re-run — took about half an hour. Peak GPU allocation 9.2 GiB. No
engine restart, no environment file, no configuration change.

It is not free on the host, though. The container was given 16 GiB of host RAM on a node that had
11.7 GiB available, and `MemAvailable` bottomed out at **1.0 GiB**: a resident idle engine on that
node was pushed about 3.4 GiB into swap. It stayed healthy — `/health` 200, zero requests running
before and after — but its next request would have paid the page-in. Send a warm-up request before
measuring anything else on that node, or run this on an idle one.
