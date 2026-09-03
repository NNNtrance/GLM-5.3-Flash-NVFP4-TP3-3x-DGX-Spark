# HAREM — GLM-5.3-Flash served the "Local Inference Lab" way, on 3x ASUS Ascent
# GX10 (GB10, sm_121, aarch64, CUDA 13).  Image tag: harem/glm53-lil:t2
#
#   docker build -t harem/glm53-lil:t2 .
#
# WHY A FULL SOURCE BUILD (build path (b))
#   The base image is vLLM 0.1.dev20051+g487ecf187.  487ecf187 is a PR branch of
#   vllm-project/vllm that left main at b908a21f (2026-08-12).  The lab fork
#   dev/jovian-judgement is rebased on main at 299ebd094 (2026-08-25).  The fork's
#   OWN 75 commits touch no compiled source at all (only setup.py: b12x 1.2.6 ->
#   1.3.0), but the 13 days of upstream csrc churn between the two bases do:
#       git diff 487ecf187 9c4dd0548 -- csrc cmake CMakeLists.txt setup.py
#       27 files changed, 4669 insertions(+), 152 deletions(-)
#   and it is not additive.  csrc/libtorch_stable/torch_bindings.cpp changes the
#   REGISTERED SCHEMAS:
#       + fused_gdn_decode_post_conv_mtp        (new op)
#       + situ_and_mul_quant                    (new op)
#       ~ situ_and_mul(..., Tensor? valid_rows) (signature changed)
#       - fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out
#   Reusing the base image's prebuilt _C_stable_libtorch.abi3.so would give the
#   fork's Python a library whose op table does not match what it calls.  So we
#   rebuild the extensions from the fork tree for sm_121a.
#
# NOTE ON `import vllm._C`
#   On CUDA the fork does NOT build a module called vllm._C (setup.py:1420-1423
#   appends only _C_stable_libtorch and _moe_C_stable_libtorch; plain vllm._C is
#   CPU/HIP only).  `import vllm._C` failing is CORRECT here and is not a build
#   failure — vllm/platforms/cuda.py:23 imports vllm._C_stable_libtorch instead.
#   STEP 6 therefore checks that the stable-libtorch .so files EXIST and that their
#   baked-in op schemas are the fork's, without importing them (a docker build has
#   no GPU and those libraries link libcuda.so.1).  The real import runs after the
#   build under `docker run --gpus all`.
#   Verified discriminators against the base image .so:
#     'situ_and_mul_quant('                                    absent  -> must become present
#     'Tensor? valid_rows'                                     absent  -> must become present
#     'fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out(' present -> must become absent

ARG BASE=vllm/vllm-openai:glm53-flash-arm64-cu130
FROM ${BASE}

# Pins, recorded so a silent source move is detectable.
ARG VLLM_FORK_COMMIT=9c4dd05487629eccb26d7166459867a3db9b099f
ARG B12X_COMMIT=887607b26f952f7cc13b5ad4ef720627eced0486
ARG MAX_JOBS=12
ARG NVCC_THREADS=2

ENV TORCH_CUDA_ARCH_LIST=12.1a \
    FLASHINFER_CUDA_ARCH_LIST=12.1a \
    CUTE_DSL_ARCH=sm_121a \
    CUDA_HOME=/usr/local/cuda \
    PATH=/usr/local/cuda/bin:${PATH}

# ---------------------------------------------------------------------------
# STEP 0 — prove the base image is the one every decision above was made against.
# ---------------------------------------------------------------------------
RUN python3 -c "import vllm, sys; v=vllm.__version__; sys.exit(0) if v=='0.1.dev20051+g487ecf187' else sys.exit('base image moved: vllm '+v)"

# ---------------------------------------------------------------------------
# STEP 1 — build tooling.  cmake is absent from the runtime image; nvcc 13.0,
# ninja, gcc and torch 2.13.0+cu130 are already there.
# GitHub's HTTP/2 endpoint answers 401 on this host/network, which breaks every
# CMake FetchContent git clone; pin git to HTTP/1.1 for the build.
# The runtime CUDA tree ships libnvrtc.so.13 but not the unversioned development
# symlink, so CMake's FindCUDAToolkit leaves CUDA_nvrtc_LIBRARY=NOTFOUND and the
# generate step fails for spinloop/cumem_allocator/fs_io_C.  Add the symlink.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ccache \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "cmake>=3.26" "setuptools-rust>=1.9.0" \
 && git config --global http.version HTTP/1.1 \
 && ln -sf libnvrtc.so.13 /usr/local/cuda/lib64/libnvrtc.so \
 && ln -sf libnvrtc-builtins.so.13.0 /usr/local/cuda/lib64/libnvrtc-builtins.so \
 && git --version && cmake --version && nvcc --version | tail -2

# The runtime CUDA include tree is a SUBSET: cusparse.h — pulled in by torch's
# ATen/cuda/CUDAContextLight.h — is absent, and DeepGEMM's _deep_gemm_C target dies
# on it ("fatal error: cusparse.h: No such file or directory").  The full headers
# ship in the pip CUDA wheels; link the missing ones in rather than patching
# include paths per sub-build.
RUN python3 - <<'PY'
from pathlib import Path
src = Path("/usr/local/lib/python3.12/dist-packages/nvidia/cu13/include")
dst = Path("/usr/local/cuda/include").resolve()
assert src.is_dir() and dst.is_dir(), (src, dst)
linked = 0
for item in sorted(src.iterdir()):
    target = dst / item.name
    if target.exists():
        continue
    target.symlink_to(item)
    linked += 1
print(f"linked {linked} missing CUDA headers into {dst}")
assert (dst / "cusparse.h").exists(), "cusparse.h still missing"
PY

# STEP 2 — the fork checkout.  The prebuilt Rust frontend from the base image is
# dropped into the tree so setup.py takes its precompiled_build_rust path
# (setup.py:62-78, 1475-1476) and no cargo toolchain is needed.
# ---------------------------------------------------------------------------
COPY vllm-lil /opt/vllm-lil
RUN cp /usr/local/lib/python3.12/dist-packages/vllm/_rust_tool_parser.abi3.so /opt/vllm-lil/vllm/ \
 && ls -la /opt/vllm-lil/vllm/_rust_*.so

# ---------------------------------------------------------------------------
# STEP 3 — build and install the fork over the base install.  No build isolation
# (torch/ninja/cmake come from the image), no dependency resolution (the image
# already satisfies them; the one genuinely new requirement, instanttensor, is
# installed explicitly in STEP 4).
# ---------------------------------------------------------------------------
RUN --mount=type=cache,target=/root/.cache/ccache \
    cd /opt/vllm-lil \
 && export CCACHE_DIR=/root/.cache/ccache CCACHE_MAXSIZE=20G \
 && VLLM_VERSION_OVERRIDE=0.1.dev0+lil.jovian.9c4dd0548 \
    MAX_JOBS=${MAX_JOBS} NVCC_THREADS=${NVCC_THREADS} \
    CMAKE_BUILD_PARALLEL_LEVEL=${MAX_JOBS} \
    pip install --no-build-isolation --no-deps --no-cache-dir . \
 && ccache -s | head -5 \
 && rm -rf /root/.cache/pip

# ---------------------------------------------------------------------------
# STEP 4 — runtime packages the fork adds.
#   instanttensor  requirements/cuda.txt, needed by --load-format instanttensor
#   b12x 1.3.0     pure Python, JIT/CuTe-DSL, exactly the version setup.py pins
# ---------------------------------------------------------------------------
# Pinned to the version PyPI served when we built (0.1.9 is the latest release as of 2026-09-04);
# the exact version installed in our image was not recorded, see docs/09-open-problems.md.
RUN pip install --no-cache-dir "instanttensor==0.1.9"
COPY b12x /opt/b12x
RUN cd /opt/b12x && pip install --no-build-isolation --no-deps --no-cache-dir . \
 && rm -rf /root/.cache/pip

# ---------------------------------------------------------------------------
# STEP 5 — a writable JIT cache root.  b12x and CuTe DSL compile on first use.
# ---------------------------------------------------------------------------
RUN mkdir -p /cache/b12x /cache/cutedsl /cache/triton
ENV B12X_CACHE_DIR=/cache/b12x

# ---------------------------------------------------------------------------
# STEP 6 — build-time exam.  NOTHING here may import a CUDA extension: a docker
# build has no GPU, and vllm._C_stable_libtorch links libcuda.so.1, so importing
# it here fails for a reason that has nothing to do with the build.  We check the
# artefacts on disk and read the registered op schemas straight out of the .so
# strings; the GPU-side import is exercised after the build by
#   docker run --rm --gpus all harem/glm53-lil:t2 ...
# ---------------------------------------------------------------------------
RUN python3 - <<'PY'
import subprocess
from pathlib import Path
import vllm

print("vllm", vllm.__version__, vllm.__file__)
assert "lil.jovian" in vllm.__version__, vllm.__version__

site = Path(vllm.__file__).parent
# The modules the fork builds on CUDA (setup.py:1420-1423).  Plain vllm._C is a
# CPU/HIP target and is legitimately absent here.
for name in ("_C_stable_libtorch.abi3.so", "_moe_C_stable_libtorch.abi3.so",
             "_rust_tool_parser.abi3.so", "cumem_allocator.abi3.so",
             "spinloop.abi3.so", "fs_io_C.abi3.so"):
    so = site / name
    assert so.is_file() and so.stat().st_size > 0, f"missing extension {so}"
    print("ok", name, so.stat().st_size)
assert not (site / "_C.abi3.so").exists() and not (site / "_C.so").exists(), \
    "vllm._C was built; expected only the stable-libtorch modules on CUDA"

# Op-table drift check, read from the schema strings baked into the library.
blob = (site / "_C_stable_libtorch.abi3.so").read_bytes()
def has(sym: str) -> bool:
    return sym.encode() in blob
assert has("situ_and_mul_quant("), "situ_and_mul_quant missing: this .so predates the fork"
assert has("Tensor? valid_rows"), "situ_and_mul valid_rows arg missing: stale .so"
assert not has("fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out("), \
    "removed op still registered: the base image .so was reused"
print("op table matches the fork csrc")

from vllm.model_executor.models.registry import ModelRegistry
assert "Glm5NextForConditionalGeneration" in ModelRegistry.get_supported_archs()
print("Glm5NextForConditionalGeneration registered")

import b12x
ops = {f"{o.group}.{o.name}" for o in b12x.list_ops()}
print("b12x ops:", len(ops))
# The ops GLM-5.3-Flash actually reaches for.
for needed in ("attention.sparse_mla", "attention.dsa_indexer",
               "attention.compressed_sparse_mla", "moe.fused_moe", "moe.ep_moe",
               "gemm.blockscaled", "comm.pcie", "quantization.nvfp4"):
    assert needed in ops, f"{needed} missing from b12x.list_ops(): {sorted(ops)}"
assert hasattr(b12x, "freeze_kernel_resolution")
print("b12x", b12x.__file__)

import instanttensor
print("instanttensor", instanttensor.__file__)
print("HAREM t2 build-time exam OK")
PY

ARG HAREM_STAMP=unknown
LABEL harem.recipe="glm53-flash-lil-b12x" \
      harem.stamp=${HAREM_STAMP} \
      harem.base.vllm="0.1.dev20051+g487ecf187" \
      harem.vllm.fork="local-inference-lab/vllm dev/jovian-judgement" \
      harem.vllm.commit=${VLLM_FORK_COMMIT} \
      harem.b12x.commit=${B12X_COMMIT}
