# GLM-5.3-Flash (NVFP4) on 3× NVIDIA DGX Spark — vLLM, TP=3 + EP, DFlash2, CUDA graphs

A complete, reproducible recipe for serving **zai-org/GLM-5.3-Flash** on three DGX Spark (GB10) nodes:
the exact image build, every launch flag with its reason, the patches we had to write (and why),
what we measured, what we tried and rejected, and what is still open. Written so that a person
**or their AI coding agent** can follow it step by step.

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
10. [CREDITS](CREDITS.md) · [LICENSES](LICENSES.md) · [CONTRIBUTING — tests we could not run; send us yours](CONTRIBUTING.md)

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
