# Licenses — what you are allowed to do

This page is a summary for readers, not legal advice. The full per-component detail, with the exact
revision we ran and the URL where we confirmed each license, is in [CREDITS.md](CREDITS.md).

**Short version.** This repository is Apache-2.0. Almost everything it stands on is Apache-2.0 or
MIT and you can use it commercially. Two things are not: the **DFlash2 draft model** (non-commercial,
no derivatives) and the **NVIDIA CUDA / cuDNN / NCCL binaries** inside the container image (NVIDIA's
own terms, restricted redistribution). Those two are why we publish instructions and patches instead
of a prebuilt image or a mirrored model.

## The table

| Component | Revision we ran | License (confirmed at) | What that means for you |
|---|---|---|---|
| This recipe: docs, `patches/`, `scripts/`, `systemd/`, `audit/`, `results/` | this repository | **Apache-2.0** ([LICENSE](LICENSE)) | Use, modify, redistribute, commercially. Keep the notice. A credit or link back is appreciated but not required. |
| `zai-org/GLM-5.3-Flash` (base model) | HF `03eb5366` | **MIT** ([model card](https://huggingface.co/zai-org/GLM-5.3-Flash)) | Free to use, including commercially. Keep the copyright notice. |
| `local-inference-lab/GLM-5.3-Flash-NVFP4` (the weights we load) | HF `9c712132` | **MIT**, Copyright (c) 2026 Z.AI Co., Ltd ([LICENSE in the checkpoint](https://huggingface.co/local-inference-lab/GLM-5.3-Flash-NVFP4/blob/main/LICENSE)) | Free to use, including commercially. Download it yourself; we do not mirror it. The model card page carries no license field — the LICENSE file inside the repository is what governs. |
| `incoai/GLM-5.3-Flash-DFlash2` (speculative draft) | HF `dc77ff1c` | **CC BY-NC-ND 4.0** ([model card](https://huggingface.co/incoai/GLM-5.3-Flash-DFlash2)) | **Non-commercial, no derivatives, attribution required.** We do **not** redistribute this draft, and the padded `config.json` we describe is a change you make to your own copy. We hold a project-specific written permission from the author for our own use; it is **non-transferable** and does not cover you. If your use goes beyond CC BY-NC-ND 4.0 — commercial serving, or publishing a modified draft — obtain your own permission from the author before you run it. You can run this whole recipe without the draft: turn speculative decoding off and lose roughly the speed the draft buys. |
| DFlash / DFlash2 reference code (`z-lab/dflash`) | not pinned; reference only | **MIT** ([repository](https://github.com/z-lab/dflash)) | Free to use. Note the split: the code is MIT, the published draft **weights** above are not. |
| `vllm/vllm-openai` base image | tag `glm53-flash-arm64-cu130`, digest `sha256:905c0293…` | **Apache-2.0** for vLLM ([LICENSE](https://github.com/vllm-project/vllm/blob/main/LICENSE)); NVIDIA terms for the CUDA stack inside it | Pull it and build on it. Do **not** redistribute the image or any image derived from it: the NVIDIA CUDA, cuDNN and NCCL libraries inside carry NVIDIA's restricted redistribution terms. Everyone builds their own. |
| `local-inference-lab/vllm` fork | branch `dev/jovian-judgement`, commit `9c4dd0548` | **Apache-2.0** ([LICENSE](https://github.com/local-inference-lab/vllm/blob/main/LICENSE)) | Use, modify, redistribute, commercially. Our patches against it are Apache-2.0 too, so the combination stays consistent. |
| `local-inference-lab/b12x` kernels | 1.3.0, commit `887607b2` | **Apache-2.0** ([repository](https://github.com/local-inference-lab/b12x)) | Same as above. The authors state it is not intended for production or datacenter use — that is their guidance, not a license restriction. |
| `local-inference-lab/lil` launcher (reference only) | commit `cb58d549` | **Apache-2.0** (read in our checkout; not re-confirmed from the web) | Not in the serving path. It rejects TP=3, which is why we ship our own launcher. |
| `instanttensor` weight loader | pinned `>=0.1.9`; latest published 0.1.9 | **Apache-2.0** ([PyPI metadata](https://pypi.org/pypi/instanttensor/json)) | Free to use. It publishes no source repository, so you get what PyPI serves. Pin an exact version if reproducibility matters to you; we did not, and that is a gap. |
| NCCL 2.29.7 (inside the base image) | 2.29.7 | **License not confirmed for the binary we run** (looked at: https://github.com/NVIDIA/nccl/blob/master/LICENSE.txt) | That file states most of the project is Apache-2.0, parts keep an original BSD license, and borrowed files carry their own text; we did not read the license files inside the image. Treat the NCCL binary as part of the NVIDIA container stack under NVIDIA's terms: use it, do not redistribute the image. |
| `autoscriptlabs/nccl-mesh-plugin` | commit `19924dcc` | **MIT** ([repository](https://github.com/autoscriptlabs/nccl-mesh-plugin)) | Free to use, including commercially. Build it yourself; we do not ship the `.so`. |
| CUDA 13.0, cuDNN, NVIDIA driver 580.173.02, DGX OS | as in [docs/00](docs/00-prerequisites.md) | **NVIDIA license terms** ([DGX Spark docs](https://docs.nvidia.com/dgx/dgx-spark/)) | Not open source. Install them from NVIDIA on your own hardware. Do not redistribute them, and do not redistribute images containing them. |
| `SeraphimSerapis/tool-eval-bench` | `2.6.1.dev39+gd3352edf5` | **MIT** ([repository](https://github.com/SeraphimSerapis/tool-eval-bench)) | Free to use. Our raw output under `results/tool-eval-bench/` is ours and Apache-2.0. |
| `EleutherAI/lm-evaluation-harness` | 0.4.9 | **MIT**, Copyright (c) 2020 EleutherAI ([LICENSE.md](https://github.com/EleutherAI/lm-evaluation-harness/blob/main/LICENSE.md)) | Free to use. |
| `run-llama/ExtractBench` | commit `e86af28c` | **Apache-2.0** ([repository](https://github.com/run-llama/ExtractBench)) | Free to use. Our driver adds a pipeline at run time and does not modify the upstream tree, so there is nothing of theirs to relicense. |
| `digchick/dgx-spark-200g-link-fix` (the CX7 hotplug finding) | n/a — we applied the finding, not the code | **MIT** ([repository](https://github.com/digchick/dgx-spark-200g-link-fix)) | Free to use. We reproduce the finding and credit it; the wording in our documents is ours. |
| Community recipes by `MiaAI-Lab` and `tonyd2wild` | see [CREDITS.md](CREDITS.md) | mostly **MIT** or **Apache-2.0** per repository; several carry no license file | We vendor **no files** from either. What we took is practice and ideas — the audit-with-expected-ranges format, fail-closed patches with tests, the retraction habit, the synthetic-versus-realistic labelling — and we say where each came from. If you copy their files rather than their ideas, check each repository's own license first; some of them do not have one, which means default copyright, not a free license. |

## What we do not redistribute, and why

- **The container image.** It contains NVIDIA CUDA, cuDNN and NCCL under restricted redistribution
  terms. You build it from the published base tag with the Dockerfiles in [`patches/`](patches/).
- **The model weights.** MIT, so redistribution would be permitted, but they are 186 GB and the
  upstream copy is authoritative. Download them at the pinned revision.
- **The DFlash2 draft weights.** CC BY-NC-ND 4.0, and our permission from the author is
  project-specific and non-transferable. Get them from the author's repository yourself, under terms
  that apply to you.

## Disclaimer

This recipe is provided **as is, without warranty of any kind**, express or implied, including
merchantability, fitness for a particular purpose and non-infringement. We measured what we measured
on our own three nodes, with the versions listed above, and we labelled the evidence for every claim.
Your hardware, firmware, driver and upstream revisions will differ, and results may differ with them.

**You are responsible for complying with the license of every upstream component you download, build
or run** — model weights, draft model, container images, engine forks, kernel libraries and NVIDIA
software. Nothing here grants you any right in anyone else's work, and our project-specific
permission for the DFlash2 draft does not extend to you.

This repository's own content — documentation, patches, scripts and measurement data — is licensed
under the **Apache License, Version 2.0**. See [LICENSE](LICENSE).
