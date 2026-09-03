#!/usr/bin/env python3
"""HAREM-TP3-LIL build-time exam for harem/glm53-lil:t3.

CPU ONLY.  A docker build has no GPU, so nothing here may touch CUDA: no
device allocation, no b12x JIT, no engine.  All five patched modules were
confirmed importable without a driver against t2 before this file was written.
"""

from __future__ import annotations

import ast
import importlib
from math import gcd
from pathlib import Path

MARKER = "HAREM-TP3-LIL"
SITE = Path("/usr/local/lib/python3.12/dist-packages/vllm")

# rel path -> number of marker occurrences the patch script must have left
EXPECTED_MARKERS = {
    "model_executor/parameter.py": 5,
    "model_executor/model_loader/weight_utils.py": 2,
    "model_executor/layers/vocab_parallel_embedding.py": 1,
    "model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py": 2,
    "models/glm5next/nvidia/model.py": 7,
}

for rel, want in EXPECTED_MARKERS.items():
    path = SITE / rel
    text = path.read_text()
    got = text.count(MARKER)
    assert got == want, f"{rel}: {got} markers, expected {want}"
    ast.parse(text, filename=str(path))
    print(f"ok markers {rel} ({got})")

# The patched modules must import with no driver present.
for mod in (
    "vllm.model_executor.parameter",
    "vllm.model_executor.layers.vocab_parallel_embedding",
    "vllm.model_executor.model_loader.weight_utils",
    "vllm.model_executor.layers.mamba.gdn.kimi_gdn_linear_attn",
    "vllm.models.glm5next.nvidia.model",
):
    importlib.import_module(mod)
    print("ok import", mod)

import torch  # noqa: E402  (after the imports above, deliberately)

from vllm.model_executor.parameter import (  # noqa: E402
    _harem_tp3_pad_then_narrow as pad_then_narrow,
)

# 1. KDA A_log / per-head vectors: 64 stored, rank 2 of 3 wants rows 44:66.
a_log = torch.arange(64, dtype=torch.float32)
shard = pad_then_narrow(a_log, 0, 44, 22)
assert tuple(shard.shape) == (22,), shard.shape
assert float(shard[0]) == 44.0 and float(shard[-1]) == 0.0, shard
assert float(shard[19]) == 63.0 and float(shard[20]) == 0.0, shard

# 2. BF16 shared expert down_proj: 2048 stored on the input dim, model wants
#    2112 = 11 * lcm(64, 3); rank 2 starts at 1408 and takes 704.
down = torch.ones(4096, 2048)
shard = pad_then_narrow(down, 1, 1408, 704)
assert tuple(shard.shape) == (4096, 704), shard.shape
assert float(shard[0, 639]) == 1.0 and float(shard[0, 640]) == 0.0, "wrong pad edge"

# 3. A shard that already fits must be untouched (no accidental padding).
full = torch.arange(12288, dtype=torch.float32)
shard = pad_then_narrow(full, 0, 8192, 4096)
assert float(shard[0]) == 8192.0 and float(shard[-1]) == 12287.0

# 4. Vocab: 154880 % 3 = 2; padding_size must have been raised to lcm(64,3)=192.
from vllm.model_executor.layers.vocab_parallel_embedding import (  # noqa: E402
    DEFAULT_VOCAB_PADDING_SIZE,
    pad_vocab_size,
)

bumped = DEFAULT_VOCAB_PADDING_SIZE * 3 // gcd(DEFAULT_VOCAB_PADDING_SIZE, 3)
assert bumped == 192, bumped
assert pad_vocab_size(154880, bumped) == 154944
assert 154944 % 3 == 0
print("ok vocab pad 154880 -> 154944 with padding_size", bumped)

# 5. The model-side decisions must be exactly the ones we intend: heads padded,
#    shared expert padded, routed moe_intermediate_size NOT padded.
model_src = (SITE / "models/glm5next/nvidia/model.py").read_text()
assert "config.num_attention_heads = _h3_padded" in model_src
assert "config.linear_num_heads = ((_h3_ln + _h3_tp - 1) // _h3_tp) * _h3_tp" in model_src
assert "_h3_step = 64 * _h3_tp_shared // _h3_gcd(64, _h3_tp_shared)" in model_src
assert "config.moe_intermediate_size =" not in model_src, (
    "routed moe_intermediate_size must stay 2048 and go through expert parallelism"
)
assert "def _h3_pad_loaded(param, loaded_weight):" in model_src
# one definition plus the three weight_loader call sites
assert model_src.count("_h3_pad_loaded(param, loaded_weight)") == 4, (
    "all three weight_loader call sites must be guarded (def + 3 calls)"
)
print("ok model.py TP=3 shape decisions")

# 6. The stock TP=3 blockers must be satisfiable: the assert and the ValueError
#    both survive, they just pass at 66 heads.  Prove the arithmetic here.
assert 66 % 3 == 0 and 66 // 3 == 22
assert 2112 % 3 == 0 and 2112 % 64 == 0 and 2112 // 3 == 704
assert 288 % 3 == 0  # routed experts shard cleanly across 3 ranks under EP
attn_src = (SITE / "models/glm5next/nvidia/attention.py").read_text()
assert "num_heads must be divisible by tensor parallel size" in attn_src, (
    "the fork's head guard vanished; the head pad may no longer be needed"
)
print("ok TP=3 arithmetic: heads 66/3=22, shared I 2112/3=704, experts 288/3=96")

print("HAREM t3 build-time exam OK")
