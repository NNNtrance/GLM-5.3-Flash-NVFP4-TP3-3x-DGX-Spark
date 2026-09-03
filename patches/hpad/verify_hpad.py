import re
p = "/usr/local/lib/python3.12/dist-packages/b12x/attention/_shared/mla/prefill.py"
s = open(p).read()
n = s.count("HAREM-B12X-PREFILL-HPAD"); assert n == 2, n
ns = {}
m = re.search(r"def _harem_hpad_heads.*?return .*?\n", s, re.S); exec(m.group(0), ns)
f = ns["_harem_hpad_heads"]; assert [f(h) for h in (22, 8, 16, 12, 24, 6)] == [24, 8, 16, 16, 24, 8]
m2 = re.search(r"def _mg_head_partitions.*?return tuple\(parts\)\n", s, re.S); exec(m2.group(0), ns)
parts = ns["_mg_head_partitions"](24, 16); assert parts == ((1, 16, 0), (1, 8, 16)), parts
assert ns["_mg_head_partitions"](22, 16) == ()
c4 = open("/usr/local/lib/python3.12/dist-packages/vllm/v1/core/kv_cache_utils.py").read().count("HAREM-TP3-LIL-C4")
t3 = open("/usr/local/lib/python3.12/dist-packages/vllm/models/glm5next/nvidia/model.py").read().count("HAREM-TP3-LIL")
assert c4 >= 1 and t3 >= 7, (c4, t3)
print(f"OK hpad: 22->24 partitions {parts}; C4 marker {c4}; t3 model.py marker {t3}")
