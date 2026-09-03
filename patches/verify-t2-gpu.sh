#!/usr/bin/env bash
# HAREM - post-build exam for harem/glm53-lil:t2 that NEEDS a GPU.
# No model, no server: it imports the CUDA extensions and JIT-compiles one tiny
# b12x kernel.  Safe to run while nothing else is on the GPU.
#
#   ./verify-t2-gpu.sh [image]
set -euo pipefail
IMAGE="${1:-harem/glm53-lil:t2}"
CACHE="${CACHE:-/var/tmp/glm53-lil-cache}"
mkdir -p "$CACHE/b12x" "$CACHE/triton"

docker run --rm -i --gpus all \
  -v "$CACHE/b12x:/cache/b12x" -v "$CACHE/triton:/root/.triton" \
  -e B12X_CACHE_DIR=/cache/b12x \
  -e CUTE_DSL_ARCH=sm_121a \
  -e TORCH_CUDA_ARCH_LIST=12.1a \
  --entrypoint python3 "$IMAGE" - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("device", torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))

import vllm
print("vllm", vllm.__version__)

# The modules the fork loads on CUDA.  vllm._C is a CPU/HIP target and is
# legitimately absent; report it, do not fail on it.
import vllm._C_stable_libtorch          # noqa: F401
import vllm._moe_C_stable_libtorch      # noqa: F401
print("stable-libtorch extensions import OK")
try:
    import vllm._C                      # noqa: F401
    print("note: vllm._C is also present")
except ModuleNotFoundError as exc:
    print("expected on CUDA:", exc)

# NOTE: dir(torch.ops._C) only lists ops that have already been ATTRIBUTE-ACCESSED
# (the namespace is lazy), so it reports 7 entries no matter what is registered.
# Probe by name instead, and cross-check against the full schema list.
def registered(name: str) -> bool:
    try:
        getattr(torch.ops._C, name)
        return True
    except (AttributeError, RuntimeError):
        return False

schemas = [s for s in torch._C._jit_get_all_schemas() if str(s).startswith("_C::")]
print("_C schemas registered:", len(schemas))
assert registered("situ_and_mul_quant"), "new op missing from the live op table"
assert registered("situ_and_mul"), "situ_and_mul missing entirely"
assert not registered("fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert_out"), \
    "removed op still live: a stale .so is being loaded"
situ = [str(s) for s in schemas if str(s).startswith("_C::situ_and_mul(")]
assert situ and "valid_rows" in situ[0], f"situ_and_mul schema is stale: {situ}"
print("live op table matches the fork csrc:", situ[0])

import vllm._custom_ops                 # noqa: F401
from vllm.platforms import current_platform
print("platform", current_platform.get_device_capability(),
      "deep_gemm_supported", current_platform.support_deep_gemm())

# --- b12x: real JIT compile of one tiny kernel ---------------------------
import b12x
from b12x.quantization import nvfp4
print("b12x nvfp4 is_supported:", nvfp4.is_supported())
m, k = 128, 128
# The CuTe DSL compiler queries the device through the CUDA DRIVER API, which needs
# a current context: allocate and synchronize FIRST, otherwise plan() dies with
# CUDA_ERROR_INVALID_CONTEXT (201) inside cutlass.utils.hardware_info.
torch.cuda.set_device(0)
x = torch.randn(m, k, dtype=torch.bfloat16, device="cuda")
gs = torch.ones(1, dtype=torch.float32, device="cuda")
torch.cuda.synchronize()
plan = nvfp4.plan(m, k)                      # host-side CuTe DSL compile
out = nvfp4.allocate_outputs(plan, device="cuda")
nvfp4.run(plan=plan, x=x, global_scale=gs, outputs=out)
torch.cuda.synchronize()
assert out.packed_a_flat.numel() > 0
assert torch.isfinite(out.scale_flat.float()).all()
print("b12x JIT OK: packed", tuple(out.packed_a_flat.shape),
      out.packed_a_flat.dtype, "scale", tuple(out.scale_flat.shape))

b12x.freeze_kernel_resolution("serving")
print("b12x.freeze_kernel_resolution('serving') OK")

from b12x.attention import sparse_mla, dsa_indexer
print("sparse_mla supported:", sparse_mla.is_supported(),
      "dsa_indexer supported:", dsa_indexer.is_supported())

import instanttensor
print("instanttensor OK")
print("HAREM t2 GPU exam OK")
PY
