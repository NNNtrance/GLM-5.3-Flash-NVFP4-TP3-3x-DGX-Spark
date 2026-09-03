"""HAREM-B12X-MOE-EP-ROUTE: with an expert map bound (EP), the b12x MoE decode
policy still plans W4A16 route_mode='direct' for the ModelOpt layout, but the
small-M direct micro kernel has no expert-map contract (_small_m_direct_supported
requires expert_map is None) and no mapped-direct launch is ever compiled for that
layout -> run_w4a16_moe raises 'planned W4A16 direct routing is unsupported for
this launch shape'.  Degrade 'direct' to 'auto' when a route_expert_map is bound so
the mapped route-pack path is taken.  One anchored edit (count==1) + one helper."""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "/usr/local/lib/python3.12/dist-packages/b12x/moe/fused_moe/_impl.py"
s = open(p).read()
MARK = "HAREM-B12X-MOE-EP-ROUTE"
if MARK in s:
    print("already patched"); sys.exit(0)
anchor = '''            route_mode=(
                plan.policy_resolution.config.w4a16_route_mode
                if plan.policy_resolution is not None
                else "auto"
            ),
        )
        return _finalize_trellis_output(binding, result)
'''
assert s.count(anchor) == 1, f"anchor occurs {s.count(anchor)} times, expected 1"
new = '''            route_mode=_harem_ep_route_mode(plan, binding),  # HAREM-B12X-MOE-EP-ROUTE
        )
        return _finalize_trellis_output(binding, result)
'''
s = s.replace(anchor, new)
helper_anchor = "def _w4a16_direct_routing_supported(query: MoeDecodeQuery) -> bool:"
assert s.count(helper_anchor) == 1
helper = '''def _harem_ep_route_mode(plan, binding) -> str:
    """HAREM-B12X-MOE-EP-ROUTE: the planned W4A16 'direct' mode targets the small-M
    direct micro kernel, which has no expert-map contract (see
    _small_m_direct_supported: expert_map must be None) and is never compiled as a
    mapped launch for the ModelOpt layout.  With a route_expert_map bound, degrade
    'direct' to 'auto' so run_w4a16_moe takes the mapped route-pack path."""
    mode = (
        plan.policy_resolution.config.w4a16_route_mode
        if plan.policy_resolution is not None
        else "auto"
    )
    if mode == "direct" and getattr(binding, "route_expert_map", None) is not None:
        return "auto"
    return mode


'''
s = s.replace(helper_anchor, helper + helper_anchor)
anchor2 = '''                resolved_fused = compile_w4a16_fused_moe(
                    size_m=token_count,
                    hidden_size=workspace.k,
                    intermediate_size=workspace.n,
                    num_experts=workspace.weight_E,
                    top_k=workspace.num_topk,
                    activation=workspace.activation,
                    apply_router_weight_on_input=bool(apply_router_weight_on_input),
                    zero_fc2_output=False,
'''
assert s.count(anchor2) == 1, f"second anchor occurs {s.count(anchor2)} times, expected 1"
new2 = anchor2.replace("zero_fc2_output=False,",
    "zero_fc2_output=_harem_mapped_zero_fc2(workspace, full_rotation),  # HAREM-B12X-MOE-EP-ROUTE")
s = s.replace(anchor2, new2)
helper2 = '''def _harem_mapped_zero_fc2(workspace, full_rotation) -> bool:
    """HAREM-B12X-MOE-EP-ROUTE: a plan whose route_E (global experts) differs from
    weight_E (local experts) serves expert-parallel launches with a bound map; its
    route-pack launch must be the zeroed-FC2 variant (run_w4a16_moe expects
    zero_fc2_output == (expert_map is not None and not full_rotation and not direct))."""
    return bool(int(workspace.route_E) != int(workspace.weight_E) and not full_rotation)


'''
s = s.replace(helper_anchor, helper2 + helper_anchor)
open(p, "w").write(s)
import py_compile; py_compile.compile(p, doraise=True)
print(f"patched: {p} marker={s.count(MARK)}")
