# Credits

Nothing in this recipe was built from nothing. Below is every external component we benefited from,
with the exact revision we ran, what we use it for, and its license as we confirmed it (URL given).
Licenses we could **not** confirm are marked as such, with the page we looked at.

Licenses are summarised for readers in [LICENSES.md](LICENSES.md). This repository's own content is
Apache-2.0 ([LICENSE](LICENSE)).

Revision discipline: an entry without a commit / HF sha / image digest is not a revision. Where the
upstream project moved after we pinned it, we say so rather than silently pointing at `main`.

---

## Model and weights

### zai-org/GLM-5.3-Flash — the base model

- **What we use it for:** the model this whole recipe serves. Everything downstream (quantized
  checkpoint, draft model, shape padding) derives from its architecture: 45 layers, 288 routed
  experts, 1 shared expert, `num_attention_heads = num_key_value_heads = linear_num_heads = 64`,
  `vocab_size 154880`, `moe_intermediate_size 2048`, `intermediate_size 12288`,
  `num_nextn_predict_layers 1`. Those 64 heads and that vocabulary are why TP=3 needs padding at all.
- **Revision:** HF commit `03eb5366` ("update pipeline_tag").
- **Link:** https://huggingface.co/zai-org/GLM-5.3-Flash
- **License:** **MIT** — confirmed on the model card metadata (`license: mit`) at
  https://huggingface.co/zai-org/GLM-5.3-Flash

### local-inference-lab/GLM-5.3-Flash-NVFP4 — the checkpoint we actually load

- **What we use it for:** the production checkpoint, 186 GB on disk, 36 safetensors shards, mixed
  precision from NVIDIA ModelOpt: routed experts NVFP4 (calibrated, with A4 input scales), attention
  and the shared expert BF16, the MTP layer (45) MXFP8. We do **not** modify its `config.json` on
  disk — shape changes go in through `--hf-overrides` — so its `SHA256SUMS` stays verifiable.
- **Revision:** HF commit `9c712132678ee8ec869db9f848042ab8314c7685` (short `9c712132`).
- **Link:** https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4
- **License:** **MIT**, `Copyright (c) 2026 Z.AI Co., Ltd` — confirmed by reading the `LICENSE` file
  inside the checkpoint at
  https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4/blob/main/LICENSE
  (the model card page itself carries no `license:` metadata field — the LICENSE file is the source).

### incoai/GLM-5.3-Flash-DFlash2 — the speculative draft model

- **What we use it for:** speculative decoding at k=7. 2.2 GB, BF16, 5 draft layers over 45 target
  layers. We pad **our local copy** of its `config.json` from 32/8 to 36/9 heads for TP=3; the stock
  file is kept as `config.json.orig`.
- **Revision in production:** HF revision `dc77ff1c` (2026-08-28). We also tried the later revision
  `bf582e4e` (2026-08-31) and measured no difference we could distinguish, so we stayed on
  `dc77ff1c` `[measured-here]`.
- **Links:** https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2 · method write-up
  https://inco.ai/blog/dflash2 · reference implementation https://github.com/z-lab/dflash
- **License:** **CC BY-NC-ND 4.0** — confirmed on the model card metadata
  (`license: cc-by-nc-nd-4.0`, `base_model: zai-org/GLM-5.3-Flash`) at
  https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2 . The card states the weights are for research
  and evaluation, and that commercial use requires separate arrangement with the authors.
  The separate reference **code** repository https://github.com/z-lab/dflash is MIT (confirmed on the
  GitHub repository page) — that is the code, not the draft weights.
- **Important, and non-transferable:** we obtained a **project-specific written permission** from the
  author for our own use of this draft. That permission covers us only. It does **not** extend to you,
  it is not sublicensed by this repository, and we do **not** redistribute the draft weights. If you
  want to use DFlash2 beyond what CC BY-NC-ND 4.0 allows you, obtain your own permission from the
  author. See [LICENSES.md](LICENSES.md).

---

## Engine and kernels

### vllm/vllm-openai — base container image

- **What we use it for:** the bottom of our image chain: aarch64 + CUDA 13.0, torch 2.13.0+cu130,
  nvcc 13.0.88, ninja, gcc, and a prebuilt Rust tool parser (`_rust_tool_parser.abi3.so`) that we
  reuse because the base image has no `rustc`.
- **Revision:** tag `glm53-flash-arm64-cu130`, digest
  `sha256:905c02933be6021301db2dc284e24e3727467aa3a0f63b41d609885778a07bce`, linux/arm64, created
  2026-08-26. The vLLM inside it reports `0.1.dev20051+g487ecf187`.
- **Links:** https://hub.docker.com/r/vllm/vllm-openai/tags (tag and digest confirmed there) ·
  project https://github.com/vllm-project/vllm
- **License:** **Apache-2.0** — confirmed at
  https://github.com/vllm-project/vllm/blob/main/LICENSE . The image also carries
  `org.opencontainers.image.source=https://github.com/vllm-project/vllm`.
- **Note:** the NVIDIA CUDA, cuDNN and NCCL libraries inside the image are under NVIDIA's own
  redistribution terms. We therefore do not redistribute any image — you build yours from this tag.

### local-inference-lab/vllm — the engine fork we run

- **What we use it for:** the actual serving engine. It carries `Glm5NextForConditionalGeneration`,
  the `modelopt_mixed` quantization path, the b12x attention/linear/MoE bindings, DFlash2 speculative
  decoding and the instanttensor loader. We build its C++/CUDA extensions from source for `sm_121a`
  rather than reusing the base image's `.so` files, because the registered operator schemas differ
  between the base image commit and this fork.
- **Revision:** branch `dev/jovian-judgement`, commit
  `9c4dd05487629eccb26d7166459867a3db9b099f` (2026-09-01), 75 commits ahead of upstream merge-base
  `299ebd094`. Build version stamp `0.1.dev0+lil.jovian.9c4dd0548`.
- **Link:** https://github.com/local-inference-lab/vllm
- **License:** **Apache-2.0** — confirmed at
  https://github.com/local-inference-lab/vllm/blob/main/LICENSE
- **Specific debt:** the KV page-rounding fix that our `HAREM-TP3-LIL-C4` patch enables was **already
  written by this fork**; it was gated on the wrong condition (an environment variable). We only
  removed the wrong gate. The fix itself is theirs.

### local-inference-lab/b12x — kernel library

- **What we use it for:** sparse-MLA attention kernels, the DSA indexer, fused and EP MoE,
  block-scaled GEMM, NVFP4 quantization and PCIe communication. Pure Python with JIT / CuTe DSL.
- **Revision:** version `1.3.0`, commit `887607b26f952f7cc13b5ad4ef720627eced0486` (2026-09-02).
  The fork's `setup.py` pins 1.2.6 by default; we moved it to 1.3.0.
- **Link:** https://github.com/local-inference-lab/b12x
- **License:** **Apache-2.0** — confirmed on the repository page
  https://github.com/local-inference-lab/b12x (and in the `LICENSE` file of our checkout).
- **Note for upstream:** the library's README states it is not intended for production or datacenter
  use. Our `HAREM-B12X-QPAD` finding below is offered in that spirit.

### local-inference-lab/lil — launcher and catalog (reference only)

- **What we use it for:** reference only. We run `lil render GLM-5.3-Flash-NVFP4` to read the lab's
  own flag set and compare it with ours. It is **not** in the production path: its launcher rejects
  TP=3 outright (64 heads are not divisible by 3), which is why we wrote our own
  [`scripts/start-lil.sh`](scripts/start-lil.sh).
- **Revision:** commit `cb58d5495ff1b36ecbc6045ece2908e4fc2950d6` (2026-09-01).
- **Link:** https://github.com/local-inference-lab/lil
- **License:** **Apache-2.0** — read in the `LICENSE` file of our local checkout. Not re-confirmed
  from the web in this pass.

### instanttensor — weight loader

- **What we use it for:** `--load-format instanttensor` for the target model. Measured 4.24 GB/s and
  a 57–58 s load for the 186 GB checkpoint `[measured-here]`. The **draft** model is deliberately
  loaded with plain `safetensors` instead — see `fix-A` below.
- **Revision:** we pin only `instanttensor>=0.1.9` in the Dockerfile. Latest published version on
  PyPI is **0.1.9** (released 2026-05-27). We did not record the exact version resolved inside the
  production image.
- **Link:** https://pypi.org/project/instanttensor/ (the package publishes no homepage or source URL)
- **License:** **Apache-2.0** — confirmed from the PyPI package metadata at
  https://pypi.org/pypi/instanttensor/json (the `license` field carries the full Apache-2.0 text;
  there are no license classifiers, and no public source repository is linked).

### NCCL

- **What we use it for:** TP=3 all-reduce across the three nodes.
- **Revision:** **2.29.7**, as shipped inside the base image (the engine logs
  `vLLM is using nccl==2.29.7`). Note that DeepEP v2 wants >= 2.30.4 and warns at startup; we do not
  use DeepEP.
- **Link:** https://github.com/NVIDIA/nccl
- **License:** **license not confirmed for the binary we run** (looked at:
  https://github.com/NVIDIA/nccl/blob/master/LICENSE.txt). That file says most of the project is
  Apache-2.0, parts retain their original BSD license, and borrowed files carry their own text — so
  "NCCL is BSD-3-Clause" is too simple a statement to make. We did not read the license files inside
  the container image itself. The NCCL build we use arrives as part of the NVIDIA container stack and
  is subject to NVIDIA's redistribution terms; treat it as such and do not redistribute the image.

### autoscriptlabs/nccl-mesh-plugin — switchless RoCE

- **What we use it for:** the three nodes are cabled directly to each other in a triangle with no
  switch; every cable is its own /24. This NCCL network plugin picks the right local NIC for each
  peer address. Set with `NCCL_NET=Mesh`, `NCCL_NET_PLUGIN=mesh`.
- **Revision:** commit `19924dcc7c571d6e260953724d394ae50bad82cf` (2026-08-04). Honest caveat: the
  `libnccl-net-mesh.so` binary we deploy was built on 2026-08-29 and we did **not** record which
  commit produced it, so the binary-to-commit link is our best reconstruction, not a verified fact.
- **Link:** https://github.com/autoscriptlabs/nccl-mesh-plugin
- **License:** **MIT** — confirmed on the repository page
  https://github.com/autoscriptlabs/nccl-mesh-plugin (and in the `LICENSE` file of our clone).

---

## Measurement tools

These are not part of the served stack. They are how we checked our own claims.

### SeraphimSerapis/tool-eval-bench

- **What we use it for:** tool-calling quality (88 scenarios with hard mode enabled, 176 points), and
  its `--ifeval-only`, `--gsm8k-only` and `--needle-only` sub-commands for IFEval, GSM8K and
  needle-in-a-haystack. Raw output in [`results/tool-eval-bench/`](results/tool-eval-bench/).
- **Revision:** `2.6.1.dev39+gd3352edf5`, installed from
  `git+https://github.com/SeraphimSerapis/tool-eval-bench.git`.
- **Link:** https://github.com/SeraphimSerapis/tool-eval-bench
- **License:** **MIT** — confirmed on the repository page
  https://github.com/SeraphimSerapis/tool-eval-bench
- **Comparability warning:** the published community comparison runs used harness **v2.1.0 with 84
  scenarios**. We ran 88 scenarios on a 2.6.1 development build. Scores across those two versions are
  not directly comparable, and we say so wherever we put our number next to theirs.

### EleutherAI/lm-evaluation-harness

- **What we use it for:** MMLU, through the `local-completions` provider against our own endpoint.
- **Revision:** **0.4.9**.
- **Link:** https://github.com/EleutherAI/lm-evaluation-harness
- **License:** **MIT**, `Copyright (c) 2020 EleutherAI` — confirmed at
  https://github.com/EleutherAI/lm-evaluation-harness/blob/main/LICENSE.md

### run-llama/ExtractBench

- **What we use it for:** document-extraction quality. Our driver adds a new `PipelineSpec` to the
  in-memory registry at runtime and does **not** modify the upstream tree.
- **Revision:** clone at commit `e86af28cfb1eab69076a95ed8eb0dd2461c96cc1` (2026-08-28).
- **Link:** https://github.com/run-llama/ExtractBench
- **License:** **Apache-2.0** — confirmed on the repository page
  https://github.com/run-llama/ExtractBench (and in the `LICENSE` file of our clone).

---

## Community recipes we learned from

Two people published DGX Spark serving recipes before us, in public, with enough detail to be
checked. We read them, disagreed with some of it, and took the following. Both are credited here by
their real GitHub identities because that is how they publish.

### tonyd2wild (Tony DeAngelo)

Profile: https://github.com/tonyd2wild · index of his GLM lanes:
https://github.com/tonyd2wild/GLM-5.3-DGX-Spark-Cookbook

What we took:

1. **The retraction practice.** When a published number turns out to be unsupported, withdraw it in a
   marked note instead of quietly restating it. His wording on a withdrawn KV ceiling figure —
   the measurement's log did not survive, so it "has been withdrawn rather than restated" — is the
   model for our own "Retracted" section.
   Source: https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-DFlash2-2x-DGX-Spark
2. **Labelling synthetic against realistic speed.** His point that acceptance is content-driven, and
   that the same engine measures 105.6 tok/s on a counting prompt and 31.5 tok/s on dense prose
   minutes apart, is why every tok/s figure in this repository carries its prompt type, temperature,
   thinking state and acceptance rate, and why our "count to 200" number is labelled a
   speculative-decoding ceiling rather than a decode rate.
   Source: https://github.com/tonyd2wild/GLM-5.3-Flash-NVFP4-1M-KV-4x-DGX-Spark
3. **The idea of padding model shapes so a 3-node TP=3 fit becomes possible at all.** His and
   FlyCockpit's three-node NVFP4 recipes are where we first met the approach of padding head counts
   and MoE intermediate size on disk, in `config.json`, so a shape that is not divisible by 3 can be
   sharded. We went the other way — padding **inside the engine** and leaving the checkpoint
   untouched — but the starting idea is theirs.
   Sources: https://github.com/tonyd2wild/Minimax-M3-NVFP-3x-DGX-Sparks-TP-3 ·
   https://github.com/FlyCockpit/GLM-5.2-Abliterated-Vision-3x-DGX-Sparks (MIT)
   **Not confirmed:** our internal notes attribute a script named `pad-tp3-config.py` to this
   lineage. We could not find such a file in either public repository (looked at the two URLs above),
   so we credit the **approach**, not a specific file.
4. His `DGX-Spark-Hard-Poweroff-Fix` and the "read these GB10 traps first" habit of his cookbook
   index — page cache, swappiness, low SM clock after a crash — shaped how we structured our own
   prerequisites document.

### MiaAI-Lab

Profile: https://github.com/MiaAI-Lab

What we took:

1. **The audit script with published expected ranges.** `AUDIT.md` ships a single command plus the
   numbers a third party should see if their install is healthy — for example
   `C1 decode: 73-76 tok/s`. That is what [`audit/`](audit/) in this repository is modelled on:
   the reader can check their own cluster against ours without asking us.
   Source: https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark/blob/main/AUDIT.md
2. **Fail-closed patches shipped with their tests.** Every runtime patch refuses loudly when the
   upstream file is not what it expected, and a test in the repository asks whether it changes
   exactly what it claims. All of our `patch_*.py` files are anchor-based and idempotent, verify that
   each anchor matches exactly once, and are followed by a `verify_*.py` build-time exam, for the
   same reason.
   Source: https://github.com/MiaAI-Lab/GLM-5.3-Flash-EXL3-2x-DGX-Sparks (`overlay/` and `tests/`)
3. **A separate `CREDITS.md` that also states the author's own contribution, with a `License Notes`
   section.** This file follows that shape.
   Source: https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark/blob/main/CREDITS.md
   That file traces a related three-node attention-group pad (8 to 9) to `localaiguyy`; we did not
   use that work, but it belongs in the lineage.
4. **tool-eval-bench comparison numbers.** The community comparison pages we put our score next to:
   https://miaai-lab.github.io/DS4FV-vs-Qwen3.8F-tool-eval-bench/ and
   https://miaai-lab.github.io/Qwen3.6-35b-vs-DSF4-tool-eval-bench/ . See the comparability warning
   in the tool-eval-bench entry above — the harness version differs.
5. `sparkDash` (https://github.com/MiaAI-Lab/sparkDash, MIT) and the community's informal speed
   protocol — temperature 0, three fixed prompts, one warm-up plus three timed runs, median decode
   tok/s — which our own speed script follows so that our numbers can be compared with theirs.

Both of them credit each other in public, by name and URL. We are continuing that.

### digchick — the 200G link fix

- **What we took:** the root cause of our worst infrastructure problem. Rebooting one node alone
  dropped the ConnectX-7 port on the node at the other end of the cable and it did not come back;
  `ip -br link` could still show UP while `ibv_devinfo` showed 2 of 4 ports active. The cause is the
  CX7 hotplug power-saving path, enabled by the presence of the flag file
  `/etc/nvidia/cx7-hotplug-enabled`. Removing that file on all three nodes fixed it, and the note
  that a future system update may re-create the file is his too.
- **Link:** https://github.com/digchick/dgx-spark-200g-link-fix
- **License:** **MIT** — confirmed on the repository page.
- **Ours on top:** the diagnosis on our own cluster, the permanent removal with backups, the
  "reboot all three, never one" rule and the pre-engine checklist (BIOS revision, GPU clock,
  CX7 link width, `ibv_devinfo` 4/4, bidirectional `ib_write_bw`).

---

## NVIDIA

- **DGX Spark / GB10 platform documentation** — DGX OS releases, firmware update path via
  `fwupdmgr` and LVFS, and the platform behaviour we rely on.
  https://docs.nvidia.com/dgx/dgx-spark/ · product page
  https://www.nvidia.com/en-us/products/workstations/dgx-spark/
- **CUDA 13.0, the 580.173.02 open kernel driver, cuDNN and NCCL** as shipped by NVIDIA in DGX OS and
  in the base container image. These are under NVIDIA's own license terms, not an OSI license, and
  they are the main reason we do not publish a prebuilt image.
- **NVIDIA ModelOpt** produced the mixed-precision quantization in the checkpoint we load.

---

## Our own work

Everything in this section was written by us, for this recipe. It is licensed **Apache-2.0** (see
[LICENSE](LICENSE)). Use it freely; a credit or a link back is appreciated.

**Patches** (all in [`patches/`](patches/); all anchor-based, idempotent, each anchor verified to
match exactly once, each inserted block carrying its marker, each image layer ending in a build-time
exam that runs without a GPU):

| Marker / file | Image | What it does |
|---|---|---|
| `HAREM-TP3-LIL` — [`patch_tp3_lil.py`](patches/patch_tp3_lil.py), [`verify_t3.py`](patches/verify_t3.py) | t3 | 17 edits in 5 files. Pad-then-narrow helper for tensor-parallel loading; the same padding in the row-parallel and sharded weight loaders; vocabulary padding to `lcm(64,3) = 192` so 154,880 becomes 154,944 (done in `__init__` so the draft model is covered too); the fused conv1d and `A_log` loaders in the shared Kimi GDN layer; head padding 64 to 66 and shared expert 2048 to 2112 in the GLM-5.3-Flash model file. It deliberately does **not** pad `moe_intermediate_size` (with EP on, the difference measured as nothing) and does not touch the checkpoint on disk. |
| `HAREM-TP3-LIL-C4` — [`patch_c4_lil.py`](patches/patch_c4_lil.py), [`fix-C4.md`](patches/fix-C4.md) | t3d | One edit. The draft KV group, not the MLA group, sets the KV bytes-per-block; at TP=3 that value is not an exact multiple of the C4 index page, and startup fails. The fork already contained the rounding fix — it was gated behind an environment variable. We removed the wrong gate and kept the model condition. Cost: +4,608 bytes per block, +0.018 %. **The fix code is the fork's, not ours.** |
| `HAREM-B12X-PREFILL-HPAD` — [`hpad/patch_hpad_b12x.py`](patches/hpad/patch_hpad_b12x.py) | t3e | One edit. The b12x sparse-MLA prefill kernel supports head counts divisible by 8; TP=3 gives 22 local heads. We zero-pad the query heads to 24, run the supported 16+8 split, and copy the first 22 rows of O and LSE back. Cost: about +9 % on prefill attention `[measured-here]`. First successful start-up. |
| `HAREM-B12X-QPAD` — [`t10/patch_qpad_fork.py`](patches/t10/patch_qpad_fork.py), [`t10/verify_t10.py`](patches/t10/verify_t10.py) | t10 | The root-cause fix. See the note below. Decode and extend plans are run at 8-aligned head counts (22 to 24), with pre-allocated workspace staging buffers so nothing is allocated at run time and CUDA graphs stay safe. |
| `HAREM-B12X-MOE-EP`, `HAREM-B12X-MOE-EP-ROUTE` — [`ep-patch/`](patches/ep-patch/), [`t4b/`](patches/t4b/) | t4, t4b | **Tried and abandoned**, kept here because a negative result is a result. Teaching the b12x MoE path to accept an expert map produced corrupt output (correctness 7/10, code exam 0/12). Production uses `marlin` with EP instead. A CPU unit test passed 6 of 6 and told us nothing — if anyone reopens this, it needs a numerical test against a reference. |
| `fix-A` — [`fix-A.md`](patches/fix-A.md), applied in [`scripts/start-lil.sh`](scripts/start-lil.sh) | launcher | Not an image patch. The draft model is loaded with `safetensors` instead of `instanttensor`, because the zero-copy mapped buffer plus our own padding copy segfaulted during weight loading. Draft load time 5.71 s. |

**Scripts, unit and documentation:** everything in [`scripts/`](scripts/), [`systemd/`](systemd/),
[`audit/`](audit/), [`tests/`](tests/), [`docs/`](docs/) and this README — the launcher, the
preflight script that waits for Docker, for `ibv_devinfo` to report 4 of 4 active ports, for the
fabric neighbours to answer, and then drops caches; the `harem-motor` systemd unit; the speed,
category and cold/warm measurement scripts; and the raw measurement data in
[`results/`](results/).

**Also ours, and not code:** the diagnosis behind each patch, the memory ladder up to
gpu-memory-utilization 0.88, the A/B results in [08 — what we tried and rejected](docs/08-what-we-tried.md),
the "reboot all three nodes, never one" rule, and the finding that setting `vm.swappiness=0` — advice
that is sound on other people's two-node layouts — locked all three of our nodes hard enough to need
a power cycle. That last one is a single unproven incident and is labelled as such.

### One finding we believe is not published anywhere else

The b12x sparse-MLA **decode** kernel computed silently wrong results at 22 heads, the shape TP=3
produces. It raised no error. It degraded quality and speculative acceptance together: the code exam
wandered between 7 and 12 out of 12 across runs, acceptance sat at 44–53 %, and output differed from
run to run. The shape was simply never exercised upstream, because TP=2 gives 32 heads and TP=4 gives
16, both already 8-aligned. Padding the decode and extend plans to 24 heads
(`HAREM-B12X-QPAD`) fixed correctness, acceptance and speed at once: code exam 12/12 three times,
acceptance 62–65 %, single-stream 56.9 tok/s against 48.3 before `[measured-here]`.

To our knowledge this has not been reported elsewhere. If you run any b12x sparse-MLA path at a head
count that is not a multiple of 8, check your output quality before you trust it — the failure is
silent. We would be glad to be shown prior art.
