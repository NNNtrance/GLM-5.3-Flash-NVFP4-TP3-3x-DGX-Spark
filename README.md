# GLM-5.3-Flash (NVFP4) on 3× NVIDIA DGX Spark — vLLM, TP=3 + EP, DFlash2, CUDA graphs

A complete, reproducible recipe for serving **zai-org/GLM-5.3-Flash** on three DGX Spark (GB10) nodes:
the exact image build, every launch flag with its reason, the patches we had to write (and why),
what we measured, what we tried and rejected, and what is still open. Written so that a person
**or their AI coding agent** can follow it step by step.

## Status (2026-09-06)

**This repository is a complete, working recipe and stays up as one, but it is not under active
development: the maintainers' main line has moved to the EXL3 recipe for the same model on the same
hardware.**

Everything here runs. The launch command, the patches, the autostart unit and the measured numbers
are the ones we served from, and the last round of work — a production candidate, a kernel-level
profile, and a list of levers closed with their numbers — is written up in
[10](docs/10-production-candidate-and-lessons.md), [11](docs/11-measured-profile.md),
[12](docs/12-what-we-closed.md) and [13](docs/13-checkpoints.md). What we are no longer doing is
pushing this path further ourselves. The short version of why is in
[12 §12](docs/12-what-we-closed.md#12-quantising-the-bf16-dense-path--the-one-open-lever-deliberately-not-taken):
half of a single-stream step is spent on weights this checkpoint leaves in BF16, and quantising them
would at best reach parity at single stream while staying ~15 % behind on concurrent throughput and
KV pool — a quality risk taken for a speed goal we can already meet another way.

The other way is
[`NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark`](https://github.com/NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark)
— the same model on the same three nodes through the EXL3 path, with measured **two-node and
three-node** tracks. Start at its
[`docs/00-start-here.md`](https://github.com/NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark/blob/main/docs/00-start-here.md).
The head-to-head numbers between the two lines, and the reason the gap is a **quantization-scope**
difference rather than a format difference, are in
[12 §12](docs/12-what-we-closed.md#12-quantising-the-bf16-dense-path--the-one-open-lever-deliberately-not-taken)
and [11 §8](docs/11-measured-profile.md#8-why-the-dense-half-was-not-quantized).

**Contributions are still welcome.** Issues and pull requests are read — corrections, results from
other GB10 clusters, and the tests we could not run ourselves
([CONTRIBUTING](CONTRIBUTING.md)) are all useful, and a correction to a published number is the most
useful of all.

> **About the name "HAREM".** HAREM is simply the name we gave our three-node setup. It is hardcoded
> in several places (image tag `harem/glm53-lil:t10`, systemd unit `harem-motor`, container
> `harem_glm53_lil`, patch markers `HAREM-*`, some function names and log lines). You can keep it.
> If you rename it, grep the whole repository first and check `docs/02-image-build.md` — some of these
> strings are matched by scripts and verification tools.

## Headline results (all at reasoning effort **low**, temperature 0 — see [why](docs/06-benchmarks.md#all-benchmarks-were-run-at-reasoning-effort-low))

| What | Result | Notes |
|---|---|---|
| Quality gates | code exam 12/12 (×3), correctness probe 10/10 | `[measured-here]` |
| MMLU (full, 14,042 q, 0-shot loglikelihood) | 85.9 ±0.3 | no generation involved |
| tool-eval-bench (8 trials, hard mode) | 87.8 ±0.9 (Pass^8 76.1 %, deployability 79–81) | DeepSeek-V4-Flash-Vision-Exp 88.6, Qwen3.8-Flash-Next-NVFP4 85.4 (MiaAI-Lab runs, older harness version, 84 vs 88 scenarios) |
| IFEval | 78.9 % prompt-strict / 85.1 % instruction | weakest: paragraph-count constraints |
| GSM8K (200 q, 8-shot CoT) | 94.0 % | |
| Needle in a haystack | 20/20 up to 997,952 tokens | full 1M context works |
| Speed, realistic (12 short English code prompts) | C1 ≈ 57–60 tok/s · C8 ≈ 22 tok/s per user, ≈ 150 tok/s total | acceptance 62–65 % on code; prose ≈ 21 tok/s (acceptance ≈ 13 %) |
| Speed depends on prompt language | same code task: English 54–63 tok/s, Turkish 41–47 tok/s | the draft model predicts English far better; tables measured with mixed-language prompts are a floor for English work |
| Speed, synthetic ("count to 200") | ≈ 93 tok/s | speculative-decoding **ceiling**; will disappoint in real use |
| KV pool | 4,321,739 tokens at gpu-memory-utilization 0.88 | 4.3 concurrent 1M-token requests |
| Boot to serving (autostart after reboot) | ≈ 5 min | all three nodes |

Settings for every number are in the linked documents. Nothing here was measured at max effort.

## Read in this order

1. [00 — Prerequisites and versions](docs/00-prerequisites.md) — update your DGX OS first; what we ran on.
2. [01 — Cluster setup](docs/01-cluster-setup.md) — network, fabric preflight, the hotplug root cause, "reboot all three".
3. [02 — Image build and patches](docs/02-image-build.md) — base image → fork → our patches, with the b12x 22→24 head story.
4. [03 — Launch command and every flag](docs/03-launch-and-flags.md) — the reasons, the A/Bs, how to adapt.
5. [04 — Autostart](docs/04-autostart.md) — systemd unit and preflight.
6. [05 — Memory ladder](docs/05-memory-ladder.md) — KV pool vs gpu-memory-utilization, free memory and swap per node.
7. [06 — Benchmarks](docs/06-benchmarks.md) and [07 — Speed](docs/07-speed.md) — realistic and synthetic, separated.
8. [08 — What we tried and rejected](docs/08-what-we-tried.md) and [09 — Open problems](docs/09-open-problems.md).
9. [Audit](audit/README.md) — run it after install; expected ranges.
10. [CREDITS](CREDITS.md) · [LICENSES](LICENSES.md) · [CONTRIBUTING — tests we could not run; send us yours](CONTRIBUTING.md) · [CHANGELOG](CHANGELOG.md)

### Closing state (2026-09-06)

Written when the line was put down. Not needed to install or run the recipe; read it before
optimising anything, or before repeating an experiment we already ran.

- [10 — The production candidate, and the five lessons we tried to carry over](docs/10-production-candidate-and-lessons.md)
  — the configuration we would ship today, what each change bought, the two verdicts we had to
  correct, and the fabric latency/bandwidth sweep.
- [11 — Where a step actually goes: the measured profile](docs/11-measured-profile.md) — kernel-level
  breakdowns of a prefill chunk and of a decode step at C1 and C8, on all three ranks, plus which
  weight families the BF16 half is made of.
- [12 — What we closed, with the numbers](docs/12-what-we-closed.md) — twelve levers, each with the
  measurement that closes it, and the five things that opened while they closed.
- [13 — The NVFP4 checkpoints, compared](docs/13-checkpoints.md) — five public quantizations, a
  silent-quality defect measured from their safetensors headers, and why the production checkpoint
  stays.

### Side studies

- [MoE kernel bench on GB10](results/kernels/moe-kernel-bench-gb10.md) — model-free, single GPU, engine
  untouched: marlin W4A16 against both FP4 tensor-core MoE paths at the production shapes, and against
  the no-expert-parallel alternative. The bench, its driver and the table generator are in
  [`bench/moe-kernels/`](bench/moe-kernels/); the raw output is in
  [`results/kernels/gb10-moebench/`](results/kernels/gb10-moebench/).
- [FP4 crossover sweep on GB10](results/kernels/fp4-crossover-sweep-gb10.md) — the adversarial re-run
  of that bench after the `cuda-exl3` author challenged it, with the design and the verdict criteria
  written **before** the run: ten batch sizes, the memory ceiling measured three ways, and the FP4
  path timed with its plumbing stripped off so a fused custom kernel's floor can be seen. Where the
  crossover is, what a custom sm_121 path could win at each operating point, the marlin cliff the
  first grid stepped over, and the correction the first bench owed. Sweep and driver in
  [`bench/moe-kernels/crossover/`](bench/moe-kernels/crossover/); raw output in
  [`results/kernels/gb10-crossover/`](results/kernels/gb10-crossover/).

## Quick path (for an AI coding agent)

```text
0. git clone https://github.com/NNNtrance/GLM-5.3-Flash-NVFP4-TP3-3x-DGX-Spark.git on the workstation and on the head node.
1. Read docs/00 and docs/01; confirm versions and ibv_devinfo 4/4 on all three nodes.
2. Download the checkpoint and the draft at the pinned revisions (docs/00).
   The draft is CC BY-NC-ND 4.0 and our permission for it does not transfer to you
   (LICENSES.md) - the recipe also runs without it, more slowly.
3. Build the NCCL mesh plugin on every node from autoscriptlabs/nccl-mesh-plugin at
   commit 19924dcc, per its README (docs/01 section 3); our binary's exact build is
   not recorded.
4. Build the image (docs/02) on the head node; ship it to the workers and prove the
   three image IDs match.
5. Copy scripts/ to ~/glm3x/ on every node; derive env from scripts/env.example per node
   with sed, never by copying (docs/03). Set FABRIC_PEERS in engine-preflight.sh per node.
6. Install systemd/harem-motor.service on every node (docs/04); reboot all three.
7. On your workstation, copy scripts/cluster.env.example to scripts/cluster.env and fill
   in your ssh targets and API address.
8. Run audit/run-audit.sh from that workstation; compare with audit/README.md ranges.
```

Evidence tiers used throughout: `[measured-here]`, `[measured-here, raw lost]`, `[reported]`, `[estimate]`, `[not tested]`.
