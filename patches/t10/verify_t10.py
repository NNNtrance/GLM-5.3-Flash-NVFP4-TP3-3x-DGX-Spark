import re
p = "/usr/local/lib/python3.12/dist-packages/vllm/v1/attention/backends/mla/b12x_mla_sparse.py"
s = open(p).read(); n = s.count("HAREM-B12X-QPAD"); assert n == 5, n
assert "self._input_num_heads = ((self._input_num_heads + 7) // 8) * 8" in s
assert s.count("*self._harem_qpad_specs()") == 2 and s.count("workspaces[-3:]") == 1
for h, want in ((22, 24), (16, 16), (32, 32), (21, 24)):
    assert ((h + 7) // 8) * 8 == want
import py_compile; py_compile.compile(p, doraise=True)
t3 = open("/usr/local/lib/python3.12/dist-packages/vllm/models/glm5next/nvidia/model.py").read().count("HAREM-TP3-LIL")
c4 = open("/usr/local/lib/python3.12/dist-packages/vllm/v1/core/kv_cache_utils.py").read().count("HAREM-TP3-LIL-C4")
hp = open("/usr/local/lib/python3.12/dist-packages/b12x/attention/_shared/mla/prefill.py").read().count("HAREM-B12X-PREFILL-HPAD")
assert t3 >= 7 and c4 >= 1 and hp == 2, (t3, c4, hp)
print(f"OK t10: QPAD marker {n}; t3 {t3}, C4 {c4}, HPAD {hp}")
