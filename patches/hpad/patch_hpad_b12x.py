"""HAREM-B12X-PREFILL-HPAD: let b12x SM120 sparse-MLA prefill accept head counts
that are not multiples of 8 (TP=3 -> 22 local heads) by zero-padding the query
heads to the next multiple of 8, running the supported MG partition (16 + 8 with
VALID_HPB=8), and copying the first `heads` rows back. Decode is untouched (it
already handles a heads%16 remainder block). One anchored edit, count==1."""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/lib/python3.12/dist-packages/b12x/attention/_shared/mla/prefill.py"
s = open(p).read()
MARK = "HAREM-B12X-PREFILL-HPAD"
if MARK in s:
    print("already patched"); sys.exit(0)
anchor = '''    num_tokens, heads, _ = q.shape
    hpb = 16
    if heads % (hpb // 2) != 0:
'''
assert s.count(anchor) == 1, f"anchor occurs {s.count(anchor)} times, expected 1"
new = '''    num_tokens, heads, _ = q.shape
    hpb = 16
    # HAREM-B12X-PREFILL-HPAD: non-8-aligned head counts (TP=3 -> 22 local heads)
    # are zero-padded up to the next multiple of 8 and routed through the
    # supported MG partition (16 + 8-tail with VALID_HPB=8); the first `heads`
    # rows of O/LSE are copied back. Padded heads have zero Q -> finite, unused.
    if heads % (hpb // 2) != 0:
        heads_pad = _harem_hpad_heads(heads, hpb)
        q_pad = torch.zeros(
            (num_tokens, heads_pad, q_head_dim), dtype=q.dtype, device=q.device
        )
        q_pad[:, :heads].copy_(q)
        sink_pad = None
        if attn_sink is not None:
            sink_pad = torch.zeros(
                (heads_pad,), dtype=attn_sink.dtype, device=attn_sink.device
            )
            sink_pad[:heads].copy_(attn_sink)
        out_pad, lse_pad = run_unified_prefill(
            q=q_pad,
            kv_cache=kv_cache,
            topk_indices=topk_indices,
            sm_scale=sm_scale,
            page_block_size=page_block_size,
            topk_length=topk_length,
            attn_sink=sink_pad,
            output=None,
            lse_out=None,
            stride_kv_block=stride_kv_block,
            extra_kv_cache=extra_kv_cache,
            extra_indices=extra_indices,
            extra_topk_length=extra_topk_length,
            extra_page_block_size=extra_page_block_size,
            stride_extra_kv_block=stride_extra_kv_block,
            workspace=None,
            scale_format=scale_format,
            model_type=model_type,
            latent_scale=latent_scale,
            fp8_rope=fp8_rope,
            latent_scale_per_token=latent_scale_per_token,
        )
        if output is None:
            output = out_pad[:, :heads].contiguous()
        else:
            output.copy_(out_pad[:, :heads])
        if lse_out is None:
            lse_out = lse_pad[:, :heads].contiguous()
        else:
            lse_out.copy_(lse_pad[:, :heads])
        return output, lse_out
    if heads % (hpb // 2) != 0:
'''
s = s.replace(anchor, new)
helper_anchor = "def _mg_head_partitions(heads: int, hpb: int = 16)"
assert s.count(helper_anchor) == 1
helper = '''def _harem_hpad_heads(heads: int, hpb: int = 16) -> int:
    """HAREM-B12X-PREFILL-HPAD: next multiple of hpb//2 (8) at or above heads."""
    unit = int(hpb) // 2
    return ((int(heads) + unit - 1) // unit) * unit


'''
s = s.replace(helper_anchor, helper + helper_anchor)
open(p, "w").write(s)
import py_compile; py_compile.compile(p, doraise=True)
print(f"patched: {p} marker={s.count(MARK)}")
