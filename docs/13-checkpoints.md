# 13 — The NVFP4 checkpoints, compared, and one defect measured from the headers

[00 — Prerequisites](00-prerequisites.md) tells you to download
`local-inference-lab/GLM-5.3-Flash-NVFP4` at a pinned revision. This document is the review behind
that instruction: five public NVFP4 quantizations of `zai-org/GLM-5.3-Flash`, what differs between
them, one silent-quality defect measured directly from their safetensors headers, and why the
production checkpoint stays.

**Nothing here required a download.** It was done with the Hugging Face API, raw repository files,
and HTTP **range reads of safetensors headers plus the individual F32 scalars we needed** — about
15 MB in total. The engine was not started, no GPU was touched, no node file was changed.
`[measured-here]`, 2026-09-05/06.

The architecture is **identical in all five repositories** — 45 layers, 64 heads, hidden 4,096, 288
routed experts, `moe_intermediate_size` 2,048, dense `intermediate_size` 12,288, 1 shared expert,
vocab 154,880, `num_nextn_predict_layers` 1. None of them changes the head count or pads anything
inside the checkpoint; the TP=3 padding in this recipe is entirely our loader's work
([02](02-image-build.md)). Sizes differ by 177–189 GiB, which is ~4 GiB per node and ~8 % of the KV
pool. **The differences that matter are in quality and integration risk, not in speed.**

---

## 1. The measurement: gate/up global scales

### 1.1 What the defect is

vLLM merges the `gate` and `up` projections of a routed expert into one tensor and takes the global
scale from `w13_weight_scale_2[:, 0]` — the **gate's**. If a checkpoint gave `gate` and `up`
different global scales, the `up` half is dequantized with the wrong multiplier. This is
[vLLM issue #54150](https://github.com/vllm-project/vllm/issues/54150), and it is a silent numerical
error, not a crash.

This project's standing verdict was: *"the mismatch is a property of the model, not of the
quantization tool."* It rested on two checkpoints that both showed the defect.

### 1.2 What we measured

For each repository, the safetensors header was read, and then only the relevant F32 scalars (4 bytes
per expert) were range-read, at four depths — layers 3, 22, 40 and 44 — covering 87–288 expert pairs
per layer.

| Repository | L3 | L22 | L40 | L44 | median &#124;r−1&#124; | worst pair ratio |
|---|---|---|---|---|---|---|
| `RedHatAI/GLM-5.3-Flash-NVFP4` | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.00 % | 1.0000 |
| `RadixArk/GLM-5.3-Flash-NVFP4` | 0.0 % | 0.0 % | 0.0 % | 0.0 % | 0.00 % | 1.0000 |
| **`local-inference-lab/GLM-5.3-Flash-NVFP4`** (production) | 0.0 % | 0.0 % | n/a | 0.0 % | 0.00 % | 1.0000 |
| `LibertAIDAI/GLM-5.3-Flash-NVFP4` | 64.7 % | 83.3 % | 69.0 % | 78.0 % | 6.7–17.7 % | 2.6000 |
| `orcarouter/GLM-5.3-Flash-Uncensored-NVFP4` | *(gated)* | — | — | — | **68.5 %** (our own earlier measurement) | 3.60 |

### 1.3 What that overturns, and what it confirms

**`[retracted]` — "the gate/up mismatch is a property of the model."** It is a **property of the
quantization tool**. `llm-compressor` and ModelOpt flows above 0.46 give `gate` and `up` a single
shared global scale; ModelOpt 0.45 and one vendor's custom export do not. The original verdict came
from measuring two checkpoints that happened to be two samples of the same class — a sampling error,
not a model property. Two consequences follow: **changing producer is a correctness fix**, and the
patch we had been carrying to work around the defect (`PATCH C3`) is **unnecessary on the production
checkpoint**.

**Confirmed, and no longer borrowed.** Two claims this project had inherited from an upstream
discussion rather than measured — that `RedHatAI` is clean by construction, and that our production
checkpoint's gate/up scales are equal — are now measurements: 0/288 mismatch at every depth tested.
An independent arm the same night, sampling 24 experts at layers 3/10/25/44, reached the same
result. `[measured-here]`

---

## 2. The five checkpoints

### 2.1 Producer, scope, format

| Repository | Producer / tool | Quantized scope | Left in BF16 | Activation quantization |
|---|---|---|---|---|
| `orcarouter/…-Uncensored-NVFP4` | custom export, `compressed-tensors` (no tool version, no calibration stated) | routed experts, layers 3–44 only | attention, shared experts, dense MLP 0–2, routers, vision tower, embed, `lm_head`; **no MTP layer at all** | **none** (W4A16; no `input_global_scale` tensors) |
| `RedHatAI/GLM-5.3-Flash-NVFP4` | `llm-compressor` `0.17.2.dev32` | routed experts 3–44 NVFP4 + MTP layer 45 experts FP8-block 128×128 | attention, shared experts, dense MLP, routers, `mlp.gate`, indexer, vision, embed, `lm_head` | **W4A4 `dynamic: local`** — computed at runtime, so there is no static scale to get wrong |
| `LibertAIDAI/GLM-5.3-Flash-NVFP4` | NVIDIA ModelOpt 0.45.0, weight-only shard streaming, **no calibration** | routed experts 3–45 (including MTP) | attention, shared experts, dense MLP, routers, mHC, vision, embed, `lm_head` | declared A16; a bolted-on `model-input-scales.safetensors` (36,288 values **taken from another vendor's calibration** of the same base) exists only to feed vLLM's fused MoE |
| `RadixArk/GLM-5.3-Flash-NVFP4` | ModelOpt 0.46.0 `43fd41a5`, abs-max + 256 tensor-scale norm | routed experts 3–44 **plus shared experts and dense MLP 0–2** | attention, `mlp.gate`, mHC, norms, vision, embed, `lm_head`; MTP layer 45 fully BF16 | **W4A4 static** (per-module `input_scale`) |
| **`local-inference-lab/GLM-5.3-Flash-NVFP4`** | ModelOpt, `MIXED_PRECISION` | routed experts 3–44 NVFP4 + MTP layer 45 experts MXFP8 g32 | attention, shared experts, dense MLP, routers, vision, embed, `lm_head` | **W4A4 static, calibrated** |

None of the five quantizes the KV cache.

### 2.2 Size, freshness, licence, evidence

| Repository | Download (GiB) | Shards | Last update | Chat template revision | Licence | Quality evidence against BF16 |
|---|---:|---:|---|---|---|---|
| `orcarouter/…-Uncensored` | 177.15 | 62 | 2026-08-31 (**gated since**) | `f12e0fe1` (stale) | MIT | **none** — the card's KLD 0.073 / PPL +3.8 % / top-1 91.7 % is against its **own FP8 uncensored parent**, not BF16 |
| `RedHatAI` | 184.26 | 11 | 2026-08-28 | `f12e0fe1` (stale; fix open as discussion #4) | **no LICENSE file, no licence field** | absolute only: GPQA-D 90.57, AIME25 86.67, GSM8K-Plat 97.74, MATH-500 94.87 — no BF16 row |
| `LibertAIDAI` | 181.30 | 121 | 2026-08-30 | `f12e0fe1` (stale) | MIT | weight-space only (cos 0.99665, rel-err 0.0925) |
| `RadixArk` | 188.98 | 38 | 2026-09-04 | `a5b45eb4` (current) | MIT | the richest, but absolute only: GSM8K 97.14 over 4 seeds (96.89–97.42), AIME-2026 92.45 over 1,920 generations, Terminal-Bench 2.1 83.1 % (74/89) |
| **`local-inference-lab`** | 185.66 | 47 | 2026-09-04 | `a5b45eb4` (current) | MIT (LICENSE file present) | a single self-reported "KLD ~0.04"; "evals to follow" never followed |

**Of roughly sixty GLM-5.3-Flash quantization repositories catalogued, exactly six publish any
BF16-referenced number.** That is the field's situation, not this vendor's.

### 2.3 The one that would break our loader

| Repository | Shared expert (2,048 → 2,112, our pad path) | Dense MLP 0–2 (12,288 / 3 = 4,096, exact) | Consequence for this recipe |
|---|---|---|---|
| `orcarouter` / `RedHatAI` / `LibertAIDAI` / **`local-inference-lab`** | BF16 | BF16 | the existing BF16 pad-then-narrow path works — this is what production runs |
| `RadixArk` | **NVFP4** (`weight` U8 [2048,2048], `weight_scale` F8_E4M3 [2048,256], `weight_scale_2` F32) | NVFP4, but splits exactly, no pad needed | **new work**: the quantized shared expert must be padded on packed U8 rows *and* on FP8 block scales. Our pad code is BF16-only, and this is precisely the failure that killed an earlier branch (`start (768) + length (384) exceeds dimension size (1024)`) |

An older explanation of ours for a related trap — *"704 / 128 = five and a half quantization
groups"* — was **withdrawn on 2026-09-01** and does not apply here either: with NVFP4 g16 the marlin
gate is `intermediate_size_per_partition % max(64, group) == 0`, and 704 % 64 = 0, so the gate is
open. `RadixArk`'s risk is not arithmetic, it is **loader engineering**.

---

## 3. Why the production checkpoint stays

Three discriminators, in order of weight.

1. **gate/up global scale.** Four were measured; two are defective. Ours is clean. This was the last
   remaining candidate for a silent quality defect in our stack and it came back negative.
2. **Calibration corpus.** `RadixArk` calibrated on `cnn_dailymail` 1,024 × 512 — a summarization
   corpus. `LibertAIDAI` and `orcarouter` did not calibrate at all. `RedHatAI` does not state a
   corpus, and does not need one for activations (its scales are dynamic). The production checkpoint
   calibrated over 294 batches across four corpora including **agentic coding**, multimodal, and an
   8,192-token "deep" set. Our traffic is agentic coding. This is a **structural** advantage, not a
   measured one — nobody has run the A/B. `[not tested]`
3. **Loader compatibility.** Our TP=3 pad path is BF16-only. Four of the five checkpoints use it as
   it stands; the fifth would require new code (§2.3).

And the cost of staying is zero: it is already on all three nodes, already integrated with the image,
the patches, the draft and the autostart unit.

**The one checkpoint worth downloading second**, if you want it, is `RedHatAI` — not because it is
better, but because it is the **fork-independent control**: plain `compressed-tensors`, so stock vLLM
loads it without our fork, and its `dynamic: local` activation scales are immune by construction to
both the #54150 and #54189 families. Note it ships **no LICENSE file**, so check before publishing
anything derived from it. `[not tested]` — we did not download it. Note also that no node in this
cluster has 184 GiB free; a second checkpoint means deleting something first.

---

## 4. The uncensored option, and why it is not open

`orcarouter/GLM-5.3-Flash-Uncensored-NVFP4` is the NVFP4 form of an **abliterated** derivative. The
chain is BF16 → uncensored **FP8** → NVFP4, so it is doubly quantized and **there is no BF16
uncensored base** anywhere in that family.

Our own rule for adopting an uncensored model is that the quality loss must be shown, against BF16,
to be at noise level. That evidence does not exist for this candidate:

| Claim | Reference | Verdict |
|---|---|---|
| KLD 0.073, PPL +3.8 %, top-1 91.7 % | against its **own FP8 uncensored parent** | not a BF16 comparison; cannot be put on the same axis as the NVFP4 or EXL3 KL figures |
| Our own gates: code exam 23/24 then 12/12, correctness 10/10 | our runs, 2026-08-31 – 09-01 | real, but these are short gates, not a fidelity measurement, and were never run against a BF16 arm |
| ExtractBench 94.51 against an H100 FP8 reference 96.46 | our run, 215-document rule | confounds three variables at once — quantization, abliteration and serving stack. Not an NVFP4 quality measurement and must not be used as one |
| Refusal rate | **never measured**, by us or by the author | the one number the whole exercise is about is missing |
| The author's own note | states that refusal removal did **not** work on this model — more data, multi-directional subspaces and layer-restricted ablation all left it unchanged, and the proposed route is targeted fine-tuning with explicit "capability regression" | the producer says the method failed here |

On top of that it is now **gated** (metadata is public, raw files return 401), it has **no MTP block
at all** (its `ignore` list stops at layer 44), it has **no input scales**, so vLLM's fused-MoE path
folds uninitialised memory unless patched, and it carries the 68.5 % gate/up defect. `[not tested]`
for everything that would settle it.

For completeness, the two-arm test that *would* settle it — same engine, same settings, same day,
production checkpoint against candidate — is about **1 h 25 min of engine time per arm** for the short
gates (correctness probe, code exam ×3, MMLU sample, a refusal probe that **does not exist yet and
would have to be written**, and a repetition/coherence probe, which is abliteration's known failure
mode and which MMLU is blind to), plus 2 h 10 min per arm if a sub-1-point claim requires full MMLU.
If the chat template changes, the baseline arm must be re-run too, because the template changes
tokenisation.

---

## 5. What this document does not say

- **No checkpoint here was benchmarked against another on this cluster.** The only new measurement is
  the gate/up scale ratio; everything else is documentation, headers and configuration files.
- **The calibration-corpus argument is untested.** It is a structural reason to prefer the production
  checkpoint, not evidence that it scores better.
- **`orcarouter` could not be re-measured** at four depths because the repository is gated; its 68.5 %
  figure is our own earlier measurement, carried forward.
- **The quality cost of marlin dropping the checkpoint's W4A4 activation scales is still unmeasured**
  — see [09 item 7](09-open-problems.md#7-marlin-drops-the-checkpoints-activation-scales-the-speed-cost-is-now-measured-the-quality-cost-is-not).
  The scales exist in our checkpoint and are correct; our MoE kernel simply does not read them,
  and doing so would cost speed, not buy it.
