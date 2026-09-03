import re, types
p = "/usr/local/lib/python3.12/dist-packages/b12x/moe/fused_moe/_impl.py"
s = open(p).read()
n = s.count("HAREM-B12X-MOE-EP-ROUTE"); assert n == 4, n
ns = {}; m = re.search(r"def _harem_ep_route_mode.*?\n    return mode\n", s, re.S); exec(m.group(0), ns)
f = ns["_harem_ep_route_mode"]
P = lambda mode: types.SimpleNamespace(policy_resolution=types.SimpleNamespace(config=types.SimpleNamespace(w4a16_route_mode=mode)))
B = lambda mapped: types.SimpleNamespace(route_expert_map=(object() if mapped else None))
assert f(P("direct"), B(True)) == "auto" and f(P("direct"), B(False)) == "direct"
assert f(P("packed"), B(True)) == "packed" and f(types.SimpleNamespace(policy_resolution=None), B(True)) == "auto"
m2 = re.search(r"def _harem_mapped_zero_fc2.*?\n    return .*?\n", s, re.S); exec(m2.group(0), ns)
g = ns["_harem_mapped_zero_fc2"]; W = lambda r, w: types.SimpleNamespace(route_E=r, weight_E=w)
assert g(W(288, 96), False) is True and g(W(96, 96), False) is False and g(W(288, 96), True) is False
assert s.count("zero_fc2_output=_harem_mapped_zero_fc2(workspace, full_rotation)") == 1
# earlier patch layers must still be intact
ep = open("/usr/local/lib/python3.12/dist-packages/vllm/model_executor/layers/fused_moe/b12x.py").read().count("HAREM-B12X-MOE-EP")
hp = open("/usr/local/lib/python3.12/dist-packages/b12x/attention/_shared/mla/prefill.py").read().count("HAREM-B12X-PREFILL-HPAD")
c4 = open("/usr/local/lib/python3.12/dist-packages/vllm/v1/core/kv_cache_utils.py").read().count("HAREM-TP3-LIL-C4")
assert ep >= 10 and hp == 2 and c4 >= 1, (ep, hp, c4)
print(f"OK t4b: route helper; EP marker {ep}, HPAD {hp}, C4 {c4}")
