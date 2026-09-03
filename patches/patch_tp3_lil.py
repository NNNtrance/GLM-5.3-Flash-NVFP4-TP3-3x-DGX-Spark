#!/usr/bin/env python3
"""HAREM-TP3-LIL — TP=3 pads for the Local Inference Lab vLLM fork.

Serves local-inference-lab/GLM-5.3-Flash-NVFP4 with tensor_parallel_size=3.
The checkpoint shape does not divide by three in four places:

    num_attention_heads / num_key_value_heads / linear_num_heads  64 % 3 = 1
    vocab_size                                              154880 % 3 = 2
    BF16 shared expert  moe_intermediate_size * n_shared_experts   2048 % 3 = 2
    DFlash2 drafter GQA                    32 Q / 8 KV  (padded on disk to 36/9)

The routed experts stay 2048 wide and are distributed by EXPERT PARALLELISM,
so moe_intermediate_size is deliberately NOT padded here: with EP on we
measured no difference between 2112 and 2048 beyond silencing a VllmConfig
check (see docs/02-image-build.md, HAREM-TP3-LIL).  Only the BF16 shared expert, which is column/row parallel
regardless of EP, is padded 2048 -> 2112 = 11 * lcm(64, 3).

Every edit asserts its anchor occurs EXACTLY ONCE and is idempotent: rerunning
the script on an already patched tree is a no-op.  Each inserted block carries
the marker HAREM-TP3-LIL.

Semantics ported from our two earlier TP=3 overlay sets (NOT copied files --
the fork's sources differ): the NVFP4-era vLLM overlay and the EXL3-era one.
New here, because the fork moved the KDA into a shared Kimi layer that the
older snapshots did not have:
  vllm/model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "HAREM-TP3-LIL"
SITE = Path("/usr/local/lib/python3.12/dist-packages/vllm")

EDITS: list[tuple[str, str, str, str]] = []


def edit(relpath: str, tag: str, old: str, new: str) -> None:
    EDITS.append((relpath, tag, old, new))


# ---------------------------------------------------------------------------
# 1. model_executor/parameter.py — pad-then-narrow for every vLLMParameter
#    loader.  This is what carries the DFlash2 drafter (36/9 heads configured,
#    32/8 stored) and the MLA q_b_proj / KDA in_proj at 66 heads.
# ---------------------------------------------------------------------------
edit(
    "model_executor/parameter.py",
    "helper",
    "logger = init_logger(__name__)\n\n\nclass BasevLLMParameter(Parameter):\n",
    '''logger = init_logger(__name__)


def _harem_tp3_pad_then_narrow(
    tensor: torch.Tensor, dim: int, start: int, length: int
) -> torch.Tensor:
    """HAREM-TP3-LIL: narrow, zero-padding first when the TP pad pushed
    shard_end past the stored dim.

    GLM-5.3-Flash at TP=3 pads attention 64->66 and the BF16 shared expert
    2048->2112, so rank 2 asks for rows the checkpoint does not contain. Those
    rows must contribute zero, which is exactly what the pad gives.
    """
    if tensor.dim() == 0:
        return tensor
    need = start + length
    cur = tensor.size(dim)
    if cur < need:
        pads = [0, 0] * tensor.dim()
        axis_from_end = tensor.dim() - 1 - dim
        pads[2 * axis_from_end + 1] = need - cur
        tensor = torch.nn.functional.pad(tensor, tuple(pads))
    return tensor.narrow(dim, start, length)


class BasevLLMParameter(Parameter):
''',
)

edit(
    "model_executor/parameter.py",
    "load_column_parallel_weight",
    """    def load_column_parallel_weight(self, loaded_weight: torch.Tensor):
        shard_size = self.data.shape[self.output_dim]
        loaded_weight = loaded_weight.narrow(
            self.output_dim, self.tp_rank * shard_size, shard_size
        )
""",
    """    def load_column_parallel_weight(self, loaded_weight: torch.Tensor):
        shard_size = self.data.shape[self.output_dim]
        # HAREM-TP3-LIL
        loaded_weight = _harem_tp3_pad_then_narrow(
            loaded_weight, self.output_dim, self.tp_rank * shard_size, shard_size
        )
""",
)

edit(
    "model_executor/parameter.py",
    "load_merged_column_weight",
    """        param_data = param_data.narrow(self.output_dim, shard_offset, shard_size)
        loaded_weight = loaded_weight.narrow(
            self.output_dim, self.tp_rank * shard_size, shard_size
        )
        assert param_data.shape == loaded_weight.shape
""",
    """        param_data = param_data.narrow(self.output_dim, shard_offset, shard_size)
        # HAREM-TP3-LIL
        loaded_weight = _harem_tp3_pad_then_narrow(
            loaded_weight, self.output_dim, self.tp_rank * shard_size, shard_size
        )
        assert param_data.shape == loaded_weight.shape
""",
)

edit(
    "model_executor/parameter.py",
    "load_qkv_weight",
    """        param_data = param_data.narrow(self.output_dim, shard_offset, shard_size)
        loaded_weight = loaded_weight.narrow(
            self.output_dim, shard_id_int * shard_size, shard_size
        )
""",
    """        param_data = param_data.narrow(self.output_dim, shard_offset, shard_size)
        # HAREM-TP3-LIL
        loaded_weight = _harem_tp3_pad_then_narrow(
            loaded_weight, self.output_dim, shard_id_int * shard_size, shard_size
        )
""",
)

edit(
    "model_executor/parameter.py",
    "load_row_parallel_weight",
    """    def load_row_parallel_weight(self, loaded_weight: torch.Tensor):
        shard_size = self.data.shape[self.input_dim]
        loaded_weight = loaded_weight.narrow(
            self.input_dim, self.tp_rank * shard_size, shard_size
        )
""",
    """    def load_row_parallel_weight(self, loaded_weight: torch.Tensor):
        shard_size = self.data.shape[self.input_dim]
        # HAREM-TP3-LIL
        loaded_weight = _harem_tp3_pad_then_narrow(
            loaded_weight, self.input_dim, self.tp_rank * shard_size, shard_size
        )
""",
)

# ---------------------------------------------------------------------------
# 2. model_executor/model_loader/weight_utils.py — the two plain-tensor
#    loaders installed with set_weight_attrs (KDA dt_bias, sinks, ...).
# ---------------------------------------------------------------------------
edit(
    "model_executor/model_loader/weight_utils.py",
    "row_parallel_weight_loader",
    """    if shard_dim is not None:
        shard_size = param.data.shape[shard_dim]
        start_idx = tp_rank * shard_size
        loaded_weight = loaded_weight.narrow(shard_dim, start_idx, shard_size)

    return default_weight_loader(param, loaded_weight)
""",
    """    if shard_dim is not None:
        shard_size = param.data.shape[shard_dim]
        start_idx = tp_rank * shard_size
        # HAREM-TP3-LIL: TP pad can push shard_end past the stored dim.
        need = start_idx + shard_size
        cur = loaded_weight.size(shard_dim)
        if cur < need:
            pads = [0, 0] * loaded_weight.dim()
            axis_from_end = loaded_weight.dim() - 1 - shard_dim
            pads[2 * axis_from_end + 1] = need - cur
            loaded_weight = torch.nn.functional.pad(loaded_weight, tuple(pads))
        loaded_weight = loaded_weight.narrow(shard_dim, start_idx, shard_size)

    return default_weight_loader(param, loaded_weight)
""",
)

edit(
    "model_executor/model_loader/weight_utils.py",
    "sharded_weight_loader",
    """        shard_size = param.data.shape[shard_axis]
        start_idx = tp_rank * shard_size
        loaded_weight = loaded_weight.narrow(shard_axis, start_idx, shard_size)

        return default_weight_loader(param, loaded_weight)
""",
    """        shard_size = param.data.shape[shard_axis]
        start_idx = tp_rank * shard_size
        # HAREM-TP3-LIL: TP pad can push shard_end past the stored dim.
        need = start_idx + shard_size
        cur = loaded_weight.size(shard_axis)
        if cur < need:
            pads = [0, 0] * loaded_weight.dim()
            axis_from_end = loaded_weight.dim() - 1 - shard_axis
            pads[2 * axis_from_end + 1] = need - cur
            loaded_weight = torch.nn.functional.pad(loaded_weight, tuple(pads))
        loaded_weight = loaded_weight.narrow(shard_axis, start_idx, shard_size)

        return default_weight_loader(param, loaded_weight)
""",
)

# ---------------------------------------------------------------------------
# 3. model_executor/layers/vocab_parallel_embedding.py — vocab 154880 % 3 = 2.
#    Bumping padding_size centrally covers the DFlash2 drafter's embedding and
#    lm_head too (same 154880 vocab), which a call-site-only fix would miss.
# ---------------------------------------------------------------------------
edit(
    "model_executor/layers/vocab_parallel_embedding.py",
    "padding_size",
    """        self.num_embeddings = num_embeddings
        self.padding_size = padding_size
        self.org_vocab_size = org_num_embeddings or num_embeddings
""",
    """        self.num_embeddings = num_embeddings
        self.padding_size = padding_size
        # HAREM-TP3-LIL: at TP=3 the default pad_to=64 leaves 154880 unchanged
        # and 154880 % 3 != 0. Raise padding_size to lcm(pad, tp) = 192 so the
        # padded vocab (154944) divides. Applies to the drafter as well.
        if self.tp_size > 1 and self.padding_size % self.tp_size != 0:
            from math import gcd

            self.padding_size = (
                self.padding_size * self.tp_size // gcd(self.padding_size, self.tp_size)
            )
        self.org_vocab_size = org_num_embeddings or num_embeddings
""",
)

# ---------------------------------------------------------------------------
# 4. model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py — the KDA layer
#    Glm5NextLinearAttention derives from.  Neither older snapshot had this
#    file, so neither of our earlier patch sets covers it.
#      A_log            stored (64,)          local param (22,)
#      fused conv1d     stored 3 x (64*128)   local param 3 x (66*128/3)
# ---------------------------------------------------------------------------
edit(
    "model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py",
    "a_log_weight_loader",
    """        loaded_weight = loaded_weight.narrow(shard_axis, start_idx, shard_size)
        return default_weight_loader(param, loaded_weight)

    return loader
""",
    """        # HAREM-TP3-LIL: A_log holds one entry per linear head. At TP=3 the
        # head count is padded 64 -> 66, so rank 2 wants rows 44:66 of a
        # 64-long tensor. The padded heads must read zero.
        need = start_idx + shard_size
        cur = loaded_weight.size(shard_axis)
        if cur < need:
            pads = [0, 0] * loaded_weight.dim()
            axis_from_end = loaded_weight.dim() - 1 - shard_axis
            pads[2 * axis_from_end + 1] = need - cur
            loaded_weight = torch.nn.functional.pad(loaded_weight, tuple(pads))
        loaded_weight = loaded_weight.narrow(shard_axis, start_idx, shard_size)
        return default_weight_loader(param, loaded_weight)

    return loader
""",
)

edit(
    "model_executor/layers/mamba/gdn/kimi_gdn_linear_attn.py",
    "fused_conv1d_weight_loader",
    """        shard_size = sharded_dims[loaded_shard_id]
        source_start = tp_rank * shard_size
        target_start = sum(sharded_dims[:loaded_shard_id])
        loaded_shard = loaded_weight[source_start : source_start + shard_size]
        param.data[target_start : target_start + shard_size].copy_(loaded_shard)
""",
    """        shard_size = sharded_dims[loaded_shard_id]
        source_start = tp_rank * shard_size
        target_start = sum(sharded_dims[:loaded_shard_id])
        # HAREM-TP3-LIL: projection_size follows the padded head count
        # (66 * 128), the checkpoint group is 64 * 128. A bare slice would
        # silently come up short and copy_ would raise on the shape.
        need = source_start + shard_size
        if loaded_weight.size(0) < need:
            pads = [0, 0] * loaded_weight.dim()
            pads[2 * (loaded_weight.dim() - 1) + 1] = need - loaded_weight.size(0)
            loaded_weight = torch.nn.functional.pad(loaded_weight, tuple(pads))
        loaded_shard = loaded_weight[source_start : source_start + shard_size]
        param.data[target_start : target_start + shard_size].copy_(loaded_shard)
""",
)

# ---------------------------------------------------------------------------
# 5. models/glm5next/nvidia/model.py — the model-side shape decisions.
# ---------------------------------------------------------------------------
edit(
    "models/glm5next/nvidia/model.py",
    "head_pad",
    """        config = vllm_config.model_config.hf_config
        self.config = config
        speculative_config = vllm_config.speculative_config
""",
    """        config = vllm_config.model_config.hf_config
        self.config = config
        # HAREM-TP3-LIL head pad. 64 attention heads do not divide by 3; round
        # up to 66 so local_heads = 22. b12x sparse-MLA runs 22 natively (16 +
        # a 6-head remainder grid, kernel.py VALID_HPB), so there is NO
        # kernel-side 22 -> 32 pad here. Checkpoint tensors stay 64 wide and
        # the loaders above zero-pad the two extra heads.
        # --hf-overrides normally sets 66 before we get here; this block is the
        # belt for linear_attn_config, which --hf-overrides cannot reach (it is
        # only read while the config object is being constructed).
        # moe_intermediate_size is deliberately NOT touched: the routed experts
        # stay 2048 wide and are distributed by expert parallelism.
        _h3_tp = get_tensor_model_parallel_world_size()
        if config.num_attention_heads % _h3_tp != 0:
            _h3_padded = (
                (config.num_attention_heads + _h3_tp - 1) // _h3_tp
            ) * _h3_tp
            config.num_attention_heads = _h3_padded
            _h3_nkv = getattr(config, "num_key_value_heads", None)
            if _h3_nkv and _h3_nkv % _h3_tp != 0:
                config.num_key_value_heads = _h3_padded
        _h3_lac = getattr(config, "linear_attn_config", None)
        if isinstance(_h3_lac, dict):
            _h3_lh = _h3_lac.get("num_heads")
            if _h3_lh and _h3_lh % _h3_tp != 0:
                _h3_lac = dict(_h3_lac)
                _h3_lac["num_heads"] = ((_h3_lh + _h3_tp - 1) // _h3_tp) * _h3_tp
                config.linear_attn_config = _h3_lac
        _h3_ln = getattr(config, "linear_num_heads", None)
        if _h3_ln and _h3_ln % _h3_tp != 0:
            config.linear_num_heads = ((_h3_ln + _h3_tp - 1) // _h3_tp) * _h3_tp
        speculative_config = vllm_config.speculative_config
""",
)

edit(
    "models/glm5next/nvidia/model.py",
    "shared_expert_pad",
    """            intermediate_size = config.moe_intermediate_size * config.n_shared_experts

            self.shared_experts = Glm5NextMLP(
""",
    """            intermediate_size = config.moe_intermediate_size * config.n_shared_experts
            # HAREM-TP3-LIL shared-expert pad. The shared expert is native
            # BF16 and column/row parallel no matter what the MoE backend does,
            # so 2048 % 3 stops the load. Pad to the next multiple of
            # lcm(64, tp) = 192 -> 2112, the width GLM-5.2 used at TP=3.
            # Replicating it instead (disable_tp) would be counted three times
            # by the all-reduce that follows the fused MoE.
            _h3_tp_shared = get_tensor_model_parallel_world_size()
            if intermediate_size % _h3_tp_shared != 0:
                from math import gcd as _h3_gcd

                _h3_step = 64 * _h3_tp_shared // _h3_gcd(64, _h3_tp_shared)
                _h3_one = config.moe_intermediate_size
                _h3_one = ((_h3_one + _h3_step - 1) // _h3_step) * _h3_step
                intermediate_size = _h3_one * config.n_shared_experts

            self.shared_experts = Glm5NextMLP(
""",
)

edit(
    "models/glm5next/nvidia/model.py",
    "load_weights_helper",
    """    def load_weights(self, weights: Iterable[tuple[str, torch.Tensor]]) -> set[str]:
        stacked_params_mapping = [
""",
    '''    def load_weights(self, weights: Iterable[tuple[str, torch.Tensor]]) -> set[str]:
        # HAREM-TP3-LIL. Second line of defence in front of every weight
        # loader: grow a checkpoint tensor to the padded model shape before the
        # loader narrows it, for the loaders that are plain functions and never
        # reach the vLLMParameter pad in model_executor/parameter.py.
        _h3_tp_size = get_tensor_model_parallel_world_size()

        def _h3_pad_loaded(param, loaded_weight):
            if not hasattr(param, "shape") or not hasattr(loaded_weight, "shape"):
                return loaded_weight
            if loaded_weight.shape == param.shape:
                return loaded_weight
            # KDA A_log: one entry per linear head, 64 -> 66 at TP=3.
            if (
                loaded_weight.dim() == 1
                and loaded_weight.numel() == 64
                and _h3_tp_size == 3
            ):
                return torch.nn.functional.pad(loaded_weight, (0, 2))
            # Per-tensor scales (input_scale, weight_scale_2) are stored (1,)
            # while the MoE parameter is (num_experts,). Both are 1-D, so the
            # dim guard below does not catch it and a scalar would be
            # zero-stretched to num_experts.
            if loaded_weight.numel() == 1:
                return loaded_weight
            if loaded_weight.dim() != param.dim():
                return loaded_weight
            # VocabParallelEmbedding.weight_loader asserts the loaded vocab dim
            # equals org_vocab_size and pads the shard itself.
            vocab = getattr(self.config, "vocab_size", None)
            if vocab and loaded_weight.dim() >= 1 and loaded_weight.shape[0] == vocab:
                return loaded_weight
            pad: list[int] = []
            for ls, ps in zip(reversed(loaded_weight.shape), reversed(param.shape)):
                if ls == ps:
                    extra = 0
                elif ls > ps and ps * _h3_tp_size >= ls:
                    extra = ps * _h3_tp_size - ls
                elif ls < ps:
                    extra = ps - ls
                else:
                    extra = 0
                pad.extend((0, extra))
            if any(pad[1::2]):
                loaded_weight = torch.nn.functional.pad(loaded_weight, tuple(pad))
            return loaded_weight

        stacked_params_mapping = [
''',
)

edit(
    "models/glm5next/nvidia/model.py",
    "load_stacked",
    """                param = params_dict[name]
                weight_loader = param.weight_loader
                weight_loader(param, loaded_weight, shard_id)
                break
""",
    """                param = params_dict[name]
                weight_loader = param.weight_loader
                # HAREM-TP3-LIL
                weight_loader(param, _h3_pad_loaded(param, loaded_weight), shard_id)
                break
""",
)

edit(
    "models/glm5next/nvidia/model.py",
    "load_expert",
    """                    weight_loader = param.weight_loader
                    weight_loader(
                        param,
                        loaded_weight,
                        name,
                        expert_id=expert_id,
                        shard_id=expert_shard_id,
                    )
""",
    """                    weight_loader = param.weight_loader
                    weight_loader(
                        param,
                        # HAREM-TP3-LIL
                        _h3_pad_loaded(param, loaded_weight),
                        name,
                        expert_id=expert_id,
                        shard_id=expert_shard_id,
                    )
""",
)

edit(
    "models/glm5next/nvidia/model.py",
    "load_default",
    """                    weight_loader = getattr(
                        param, "weight_loader", default_weight_loader
                    )
                    weight_loader(param, loaded_weight, **kwargs)
""",
    """                    weight_loader = getattr(
                        param, "weight_loader", default_weight_loader
                    )
                    # HAREM-TP3-LIL
                    weight_loader(param, _h3_pad_loaded(param, loaded_weight), **kwargs)
""",
)

edit(
    "models/glm5next/nvidia/model.py",
    "mamba_state_shape",
    """        return MambaStateShapeCalculator.kda_state_shape(
            tp_size,
            hf_config.linear_num_heads,
""",
    """        # HAREM-TP3-LIL: the KV cache shape must use the same padded head
        # count the model was built with, or the state buffers are one head
        # group short on every rank.
        _h3_lin_heads = hf_config.linear_num_heads
        if _h3_lin_heads % tp_size != 0:
            _h3_lin_heads = ((_h3_lin_heads + tp_size - 1) // tp_size) * tp_size
        return MambaStateShapeCalculator.kda_state_shape(
            tp_size,
            _h3_lin_heads,
""",
)


def main() -> int:
    changed = 0
    skipped = 0
    by_file: dict[Path, str] = {}

    for relpath, tag, old, new in EDITS:
        path = SITE / relpath
        if path not in by_file:
            if not path.is_file():
                raise SystemExit(f"{path}: not in the image")
            by_file[path] = path.read_text()
        src = by_file[path]
        if new in src:
            print(f"skip   {relpath}:{tag} (already patched)")
            skipped += 1
            continue
        count = src.count(old)
        if count != 1:
            raise SystemExit(
                f"{relpath}:{tag}: anchor occurs {count} times, expected exactly 1\n"
                f"--- anchor ---\n{old}"
            )
        by_file[path] = src.replace(old, new, 1)
        print(f"patch  {relpath}:{tag}")
        changed += 1

    for path, src in by_file.items():
        path.write_text(src)

    # Every touched file must carry the marker and must still parse.
    import ast

    for path in by_file:
        text = path.read_text()
        if MARKER not in text:
            raise SystemExit(f"{path}: marker {MARKER} absent after patching")
        ast.parse(text, filename=str(path))
    print(f"{MARKER}: {changed} edits applied, {skipped} already present, "
          f"{len(by_file)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
