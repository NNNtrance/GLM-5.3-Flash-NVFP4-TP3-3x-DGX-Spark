#!/usr/bin/env python3
"""HAREM-TP3-LIL-C4 build-time exam. CPU ONLY: no GPU in a docker build."""
import ast, inspect, sys
from pathlib import Path

MARKER = "HAREM-TP3-LIL-C4"
SITE = Path("/usr/local/lib/python3.12/dist-packages/vllm")
KVU = SITE / "v1/core/kv_cache_utils.py"

src = KVU.read_text()
assert src.count(MARKER) == 1, f"{MARKER} count = {src.count(MARKER)}"
ast.parse(src, filename=str(KVU))
assert "if contains_glm5_next_mla:" in src
assert 'os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None\n        and contains_glm5_next_mla' not in src
print("ok marker + ungated round-up in", KVU.name)

# t3's own markers must survive this layer untouched.
for rel, want in {
    "model_executor/parameter.py": 5,
    "model_executor/model_loader/weight_utils.py": 2,
    "model_executor/layers/vocab_parallel_embedding.py": 1,
    "model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py": 2,
    "models/glm5next/nvidia/model.py": 7,
}.items():
    got = (SITE / rel).read_text().count("HAREM-TP3-LIL")
    assert got == want, f"{rel}: {got} HAREM-TP3-LIL markers, expected {want}"
print("ok t3 HAREM-TP3-LIL markers intact")

from vllm.v1.core.kv_cache_utils import _get_kv_cache_bytes_per_block
body = inspect.getsource(_get_kv_cache_bytes_per_block)
assert MARKER in body and "if contains_glm5_next_mla:" in body
print("ok live function carries the patch")

# --- unit check of the arithmetic, with the numbers measured on rank 0 -------
from vllm.utils.math_utils import round_up
IPB = 64 * 132                      # pooled_indexer.py:50
assert IPB == 8448
BLOCK = 3328                        # interface.py:974 log
DRAFT_LAYERS, DRAFT_BPT = 5, 1536   # 3 local KV heads * 2 * 128 * 2 B, TP=3
measured = BLOCK * DRAFT_LAYERS * DRAFT_BPT
assert measured == 25_559_040, measured
assert measured % IPB == 3840, measured % IPB      # the observed remainder
padded = round_up(measured, IPB)
assert padded == 25_563_648, padded
assert padded % IPB == 0 and padded - measured == 4608
print(f"ok arithmetic: {measured:,} -> {padded:,} (+{padded-measured} B, "
      f"{100*(padded-measured)/measured:.3f}%), {padded}//{IPB} = {padded//IPB}")

# The MLA page itself was always clean; the pool stride was not.
MLA_PAGE = BLOCK * (528 + 33)
assert MLA_PAGE == 1_867_008 and MLA_PAGE % IPB == 0
assert BLOCK * 528 % IPB == 0 and BLOCK * 33 % IPB == 0
print("ok MLA page 1,867,008 = 221 x 8448 (record and tail both clean)")

# pooled_indexer's own check must now pass on the padded stride.
subpages = BLOCK // (4 * 64)
assert padded >= BLOCK * 528 + subpages * IPB          # tail fits
assert padded % IPB == 0                               # C4 page check
print(f"ok pooled_indexer gate: subpages={subpages} parent_stride_pages={padded//IPB}")
print("HAREM t3d build-time exam OK")
