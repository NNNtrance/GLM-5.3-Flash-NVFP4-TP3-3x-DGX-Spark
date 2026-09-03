import os, torch, vllm
from vllm.model_executor.layers.fused_moe.config import FusedMoEParallelConfig
import vllm.model_executor.layers.fused_moe.b12x as b12x_moe

src = os.path.join(
    os.path.dirname(vllm.__file__),
    "model_executor/layers/fused_moe/b12x.py",
)
text = open(src, encoding="utf-8").read()

# 1. the patch is in, and the old hard stops are gone
assert "HAREM-B12X-MOE-EP" in text, "marker missing"
assert 'raise ValueError("b12x TP MoE does not support expert maps")' in text, \
    "the TP-only guard must survive for the EP-off path"
assert "route_expert_map=route_expert_map" in text, "bind hook not wired"
assert "not moe_parallel_config.use_ep" not in text, "EP still rejected"

# 2. the parallel-config gate now admits EP and still refuses all2all / EPLB
def cfg(**kw):
    base = dict(
        tp_size=1, pcp_size=1, dp_size=1, ep_size=1,
        tp_rank=0, pcp_rank=0, dp_rank=0, ep_rank=0, sp_size=1,
        use_ep=False, all2all_backend="allgather_reducescatter", enable_eplb=False,
    )
    base.update(kw)
    return FusedMoEParallelConfig(**base)

E = b12x_moe.B12xExperts
assert E._supports_parallel_config(cfg(tp_size=3)), "TP=3 must stay supported"
assert E._supports_parallel_config(cfg(use_ep=True, ep_size=3)), \
    "TP=3 + EP must be supported"
assert not E._supports_parallel_config(cfg(use_ep=True, ep_size=3, enable_eplb=True)), \
    "EPLB must stay unsupported"
assert not E._supports_parallel_config(
    cfg(use_ep=True, ep_size=6, dp_size=2, all2all_backend="deepep_low_latency")
), "all2all dispatch must stay unsupported"

# 3. EP selects the W4A16 recipe (b12x binds expert maps only on W4A16 plans)
assert b12x_moe._b12x_ep_w4a16_mode("nvfp4") == ("w4a16", "modelopt_nvfp4", "w13")

# 4. b12x really exposes the hook we bind to
import inspect
from b12x.moe.fused_moe._impl import TPMoEScratchPlan
assert "route_expert_map" in inspect.signature(TPMoEScratchPlan.bind).parameters, \
    "installed b12x has no route_expert_map bind hook"
from b12x.moe import ep_moe  # the sanctioned EP op must at least import
assert hasattr(ep_moe, "prepare_expert_map")

# 5. the id-remap / mask logic itself (288 global -> 96 local, ep_size 3)
emap = torch.full((288,), -1, dtype=torch.int32)
emap[96:192] = torch.arange(96, dtype=torch.int32)          # this is rank 1
b12x_moe._b12x_ep_check_expert_map(
    emap, global_num_experts=288, local_num_experts=96
)
l2g = b12x_moe._b12x_ep_local_to_global(emap)
assert torch.equal(l2g, torch.arange(96, 192, dtype=torch.int32))
ids = torch.tensor([[0, 96, 191, 287]], dtype=torch.int32)
local = b12x_moe._b12x_ep_map_topk_to_local(ids, emap)
assert torch.equal(local, torch.tensor([[-1, 0, 95, -1]], dtype=torch.int32))
print("t4 EP patch verification: OK")
