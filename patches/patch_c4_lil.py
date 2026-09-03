#!/usr/bin/env python3
"""HAREM-TP3-LIL-C4 — keep the block-outermost pool stride an exact number of
GLM C4 index pages.

Measured on rank 0 (probe print before pooled_indexer.py:314):

    shape=(1505, 3328, 528)  stride=(25559040, 528, 1)
    semantic=1757184  tail=109824  need=1867008  index_page_bytes=8448
    parent_stride_bytes=25559040   25559040 % 8448 = 3840   -> ValueError

Chain that produces that stride:
  kv_cache_utils.py:1297-1303  bytes_per_block = max over groups of
      sum(page_size_bytes of the group's layers)
      -> the DFlash draft group wins: 5 layers x 3328 tok x 1536 B/tok
         = 25,559,040 (drafter bf16 KV at TP=3: 3 local KV heads x 2 x 128 x 2 B)
  kv_cache_utils.py:1387-1388  interleaved_block_stride = bytes_per_block
      (BLHNC is block-outermost)
  kv_cache_utils.py:1420-1424  compute_layout_strides(fixed_strides=(None,
      interleaved_block_stride, ...)) -> KVCacheTensor.block_stride
  kv_cache_interface.py:326,352-358  create_kv_cache_views -> the MLA view's
      stride(0)
  pooled_indexer.py:314-317  requires stride(0) % _INDEX_PAGE_BYTES == 0

8448 = 2^8 * 3 * 11. Block 3328 = 2^8 * 13 and the draft's 7680 = 2^9 * 15 carry
no factor 11, so their product is not a multiple of 8448. At TP=2/4 the drafter's
per-token bytes and the chosen block differ, which is why the lab never hit it.

THE FIX ALREADY EXISTS IN THIS FORK — it is merely gated behind an unrelated
experiment flag. kv_cache_utils.py:1310-1319 rounds bytes_per_block up to
64*132 when `VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE` is set, with the fork's own
comment: "The block-outermost pool stride must preserve the index-page unit even
when a larger recurrent-state group determines pool capacity." That is exactly
our failure. We drop the env-var condition and keep the model condition.

Setting VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE instead would ALSO flip
kv_cache_utils.py:1931-1932 into the split-cache grouping path, a far larger
behaviour change. Forcing --block-size 5632 is rejected: it pads the KDA/mamba
page by ~79%.

Before -> after for this configuration:
    bytes_per_block 25,559,040 -> 25,563,648   (+4,608 B/block, +0.018%)
    25,563,648 = 8448 * 3026, remainder 0
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MARKER = "HAREM-TP3-LIL-C4"
SITE = Path("/usr/local/lib/python3.12/dist-packages/vllm")
TARGET = SITE / "v1/core/kv_cache_utils.py"

OLD = '''    if (
        os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None
        and contains_glm5_next_mla
    ):
        # GLM-5.3 stores two 64-row by 132-byte FP8 C4 index pages in the
        # target MLA page tail when the target block contains 512 tokens.
        # The block-outermost pool stride must preserve the index-page unit
        # even when a larger recurrent-state group determines pool capacity.
        glm_c4_index_page_bytes = 64 * 132
        bytes_per_block = round_up(bytes_per_block, glm_c4_index_page_bytes)
    return bytes_per_block
'''

NEW = '''    if contains_glm5_next_mla:
        # HAREM-TP3-LIL-C4: this round-up was gated behind
        # VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE, but the invariant it protects is
        # unconditional -- pooled_indexer.py:314 rejects any parent-page stride
        # that is not a whole number of C4 index pages, and this function IS
        # that stride (:1387-1388 feed it to compute_layout_strides as
        # interleaved_block_stride under a block-outermost layout).
        # At TP=3 the DFlash draft group sets bytes_per_block to
        # 5 layers * 3328 tokens * 1536 B = 25,559,040, and
        # 25,559,040 % 8448 = 3840 because 8448 = 2^8*3*11 while 3328 = 2^8*13
        # and 7680 = 2^9*15 contribute no factor 11. Rounding up costs
        # 4,608 B per block here (+0.018%).
        # GLM-5.3 stores two 64-row by 132-byte FP8 C4 index pages in the
        # target MLA page tail when the target block contains 512 tokens.
        # The block-outermost pool stride must preserve the index-page unit
        # even when a larger recurrent-state group determines pool capacity.
        glm_c4_index_page_bytes = 64 * 132
        bytes_per_block = round_up(bytes_per_block, glm_c4_index_page_bytes)
    return bytes_per_block
'''


def main() -> int:
    if not TARGET.is_file():
        raise SystemExit(f"{TARGET}: not in the image")
    src = TARGET.read_text()
    if NEW in src:
        print(f"skip   {TARGET.name} (already patched)")
    else:
        count = src.count(OLD)
        if count != 1:
            raise SystemExit(
                f"{TARGET}: anchor occurs {count} times, expected exactly 1"
            )
        src = src.replace(OLD, NEW, 1)
        TARGET.write_text(src)
        print(f"patch  {TARGET.name}: C4 pool-stride round-up ungated")

    text = TARGET.read_text()
    if MARKER not in text:
        raise SystemExit(f"{TARGET}: marker {MARKER} absent after patching")
    ast.parse(text, filename=str(TARGET))
    # The env-gated form must be gone; the model-gated form must be present.
    assert 'os.getenv("VLLM_GLM53_SPLIT_TARGET_BLOCK_SIZE") is not None\n        and contains_glm5_next_mla' not in text
    assert "if contains_glm5_next_mla:" in text
    print(f"{MARKER}: 1 edit, 1 file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
