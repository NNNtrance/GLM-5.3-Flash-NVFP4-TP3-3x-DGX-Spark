#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Add expert-parallel (EP) support to the b12x modular MoE backend.

Target file
-----------
``vllm/model_executor/layers/fused_moe/b12x.py`` of the Local Inference Lab
vLLM fork (``dev/jovian-judgement`` @ 9c4dd0548).

Why
---
The fork wires ``b12x.moe.fused_moe`` in TP-only mode: ``B12xExperts``
rejects every parallel config with ``use_ep`` and raises on a non-``None``
``expert_map``.  With ``--moe-backend b12x`` this makes
``--enable-expert-parallel`` a hard error, so GLM-5.3-Flash-NVFP4 at TP=3
either pads ``moe_intermediate_size`` 2048 -> 2112 (2048 % 3 != 0) or gives
up on b12x.

What b12x actually offers
-------------------------
Two entry points accept a global->local expert map, and both end up in the
same kernel, ``b12x/moe/_shared/kernels/w4a16/kernel.py:run_w4a16_moe``:

* ``b12x.moe.ep_moe`` (``ep_moe/_impl.py:504`` -> ``run_w4a16_moe(...,
  expert_map=...)``) -- a separate planner with its own scratch layout;
* ``b12x.moe.fused_moe`` bind hook ``route_expert_map=``
  (``fused_moe/_impl.py:923-935`` ``TPMoEScratchPlan.bind``, forwarded at
  ``_impl.py:12312``) -- the plan/bind/run path the fork already uses.

Both are gated to W4A16: ``fused_moe/_impl.py:2862`` raises
``"route_expert_map is only supported for W4A16 plans"`` and
``ep_moe/_impl.py:194`` raises ``"replicated-input EP requires a W4A16
weight plan"``.  There is no expert-map path for the NVFP4 W4A4 recipe.

This patch therefore
--------------------
1. keeps ``b12x.moe.fused_moe`` (no new op, no second scratch planner, the
   fork's plan cache / warmup / CUDA-graph discipline are reused);
2. switches the recipe to W4A16 *only when EP is on* -- same FP4 weights,
   same ``modelopt_nvfp4`` source format, BF16 activations;
3. sizes the route metadata for the global expert namespace
   (``Caps.route_num_experts = global_num_experts``) while the weights stay
   local (``weight_E = local_num_experts``);
4. hands ``layer.expert_map`` to the kernel as ``route_expert_map``.

With EP off, every call is byte-identical to the original: the new kwargs
default to ``0`` / ``None``, which is exactly what the file passes today.

Usage
-----
    python3 patch_b12x_moe_ep.py --target /path/to/fused_moe/b12x.py
    python3 patch_b12x_moe_ep.py            # autodetect installed vLLM

Idempotent: re-running on an already patched file is a no-op (exit 0).
Every anchor must match exactly once or the patch aborts without writing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MARKER = "HAREM-B12X-MOE-EP"


# ---------------------------------------------------------------------------
# edits: (name, anchor, replacement); every anchor must occur exactly once
# ---------------------------------------------------------------------------

EDITS: list[tuple[str, str, str]] = []


# --- 1. module-level helpers ------------------------------------------------
EDITS.append(
    (
        "helpers",
        """        scale = scale[:, 0]
    return scale.to(dtype=torch.float32).contiguous()


class B12xExperts(mk.FusedMoEExpertsModular):
""",
        '''        scale = scale[:, 0]
    return scale.to(dtype=torch.float32).contiguous()


# --- @MARKER@ begin: expert-parallel helpers ---------------------------
#
# b12x accepts a global->local expert map only on W4A16 plans; see
# b12x/moe/fused_moe/_impl.py:2862 ("route_expert_map is only supported for
# W4A16 plans") and b12x/moe/ep_moe/_impl.py:194.  Expert parallelism
# therefore selects the A16 activation recipe for the same FP4 weights.


def _b12x_ep_w4a16_mode(weight_quant_dtype: Any) -> tuple[str, str, str]:
    """Return the (quant_mode, source_format, w13_layout) W4A16 recipe."""
    try:
        return _B12X_MOE_MODES[(weight_quant_dtype, None)]
    except KeyError as exc:
        raise ValueError(
            "b12x MoE expert parallelism needs a W4A16 recipe, which is not "
            f"defined for {weight_quant_dtype!r} weights"
        ) from exc


def _b12x_ep_check_expert_map(
    expert_map: torch.Tensor | None,
    *,
    global_num_experts: int,
    local_num_experts: int,
) -> torch.Tensor:
    """Validate one EP rank's global->local expert map.

    Mirrors ``b12x.moe.ep_moe.prepare_expert_map``: contiguous int32 of
    ``global_num_experts`` entries, ``-1`` for remote experts, and every
    local slot named exactly once.  Reads values on the host, so it must run
    outside CUDA graph capture.
    """
    if expert_map is None:
        raise ValueError("b12x MoE expert parallelism requires an expert map")
    if not isinstance(expert_map, torch.Tensor):
        raise TypeError("expert_map must be a torch.Tensor")
    if expert_map.dtype != torch.int32:
        raise TypeError(f"expert_map must be int32, got {expert_map.dtype}")
    if expert_map.ndim != 1 or not expert_map.is_contiguous():
        raise ValueError("expert_map must be a contiguous rank-1 tensor")
    global_num_experts = int(global_num_experts)
    local_num_experts = int(local_num_experts)
    if int(expert_map.numel()) != global_num_experts:
        raise ValueError(
            "expert_map must have one entry per global expert: expected "
            f"{global_num_experts}, got {int(expert_map.numel())}"
        )
    if not 0 < local_num_experts <= global_num_experts:
        raise ValueError(
            f"local_num_experts={local_num_experts} is not in "
            f"(0, {global_num_experts}]"
        )
    values = expert_map.detach().to("cpu").tolist()
    invalid = [value for value in values if value < -1 or value >= local_num_experts]
    if invalid:
        raise ValueError(
            "expert_map values must be -1 or a local expert id; found "
            f"{invalid[0]} for local_num_experts={local_num_experts}"
        )
    mapped = sorted(value for value in values if value >= 0)
    if mapped != list(range(local_num_experts)):
        raise ValueError(
            "expert_map must name every local expert id exactly once"
        )
    return expert_map


def _b12x_ep_local_to_global(expert_map: torch.Tensor) -> torch.Tensor:
    """int32[local_num_experts] holding each local slot's global id."""
    global_ids = torch.nonzero(expert_map >= 0, as_tuple=False).flatten()
    local_ids = expert_map[global_ids].to(torch.int64)
    out = torch.empty_like(global_ids)
    out[local_ids] = global_ids
    return out.to(torch.int32)


def _b12x_ep_map_topk_to_local(
    topk_ids: torch.Tensor,
    expert_map: torch.Tensor,
) -> torch.Tensor:
    """Reference global->local top-k remap (-1 marks a remote route).

    The device kernel performs this itself -- packed routing applies the map
    inside ``pack_topk_routes_by_expert`` and small-M direct routing inside
    the tile prologue.  This host-side twin exists so the mapping contract
    can be unit-tested without a GPU.
    """
    return expert_map.to(torch.int64)[topk_ids.to(torch.int64)].to(torch.int32)


# --- @MARKER@ end -------------------------------------------------------


class B12xExperts(mk.FusedMoEExpertsModular):
'''.replace("@MARKER@", MARKER),
    )
)


# --- 2. _run_b12x_moe_plan: forward route_expert_map ------------------------
EDITS.append(
    (
        "_run_b12x_moe_plan",
        """    output: torch.Tensor,
    unit_scale_contract: bool,
) -> None:
    fused_moe = _require_b12x_fused_moe()

    binding = fused_moe.bind(
        plan,
        scratch=scratch,
        a=hidden_states,
        experts=prepared,
        topk_weights=topk_weights,
        topk_ids=topk_ids,
        output=output,
        input_scales_static=True,
        unit_scale_contract=unit_scale_contract,
    )
""",
        f"""    output: torch.Tensor,
    unit_scale_contract: bool,
    # {MARKER}: None keeps the TP contract byte-identical (bind's default).
    route_expert_map: torch.Tensor | None = None,
) -> None:
    fused_moe = _require_b12x_fused_moe()

    binding = fused_moe.bind(
        plan,
        scratch=scratch,
        a=hidden_states,
        experts=prepared,
        topk_weights=topk_weights,
        topk_ids=topk_ids,
        output=output,
        input_scales_static=True,
        unit_scale_contract=unit_scale_contract,
        route_expert_map=route_expert_map,
    )
""",
    )
)


# --- 3. __init__: EP state + forced W4A16 recipe ----------------------------
EDITS.append(
    (
        "__init__",
        """        except KeyError as exc:
            raise ValueError(
                f"unsupported b12x MoE quantization scheme {scheme}"
            ) from exc
        self._prepared_experts: Any | None = None
""",
        f"""        except KeyError as exc:
            raise ValueError(
                f"unsupported b12x MoE quantization scheme {{scheme}}"
            ) from exc
        # --- {MARKER} begin: expert-parallel state ------------------------
        parallel_config = moe_config.moe_parallel_config
        self._ep_enabled = bool(parallel_config.use_ep)
        self._ep_global_num_experts = (
            int(moe_config.num_experts) if self._ep_enabled else 0
        )
        self._ep_local_num_experts = 0
        self._ep_expert_map: torch.Tensor | None = None
        self._ep_expert_map_ptr = 0
        self._ep_local_to_global: torch.Tensor | None = None
        # b12x binds an expert map only on W4A16 plans, so EP selects the
        # A16 activation recipe over the very same FP4 weights.  The
        # checkpoint's calibrated input_scale is simply unused: BF16
        # activations need no activation global scale.
        if self._ep_enabled:
            (
                self._quant_mode,
                self._source_format,
                self._w13_layout,
            ) = _b12x_ep_w4a16_mode(quant_config.weight_quant_dtype)
            logger.info_once(
                "b12x MoE: expert parallelism is on (ep_size=%d, %d global "
                "experts); using the W4A16 recipe (%s/%s) because b12x binds "
                "expert maps only on W4A16 plans.",
                int(parallel_config.ep_size),
                self._ep_global_num_experts,
                self._quant_mode,
                self._source_format,
            )
        # --- {MARKER} end ---------------------------------------------------
        self._prepared_experts: Any | None = None
""",
    )
)


# --- 4. _supports_parallel_config -------------------------------------------
EDITS.append(
    (
        "_supports_parallel_config",
        """        return (
            not moe_parallel_config.use_ep
            and moe_parallel_config.ep_size == 1
            and not moe_parallel_config.use_all2all_kernels
            and not moe_parallel_config.enable_eplb
        )
""",
        f"""        # {MARKER}: EP over a single engine instance is supported through
        # the W4A16 route_expert_map hook.  All2all dispatch (DP/PCP/SP) and
        # EPLB reshuffling remain unsupported: the backend consumes the
        # replicated-input contract and a static expert map.
        return (
            not moe_parallel_config.use_all2all_kernels
            and not moe_parallel_config.enable_eplb
        )
""",
    )
)


# --- 5. supports_expert_map -------------------------------------------------
EDITS.append(
    (
        "supports_expert_map",
        """    def supports_expert_map(self) -> bool:
        return False
""",
        f"""    def supports_expert_map(self) -> bool:
        # {MARKER}: true only in the EP (W4A16) configuration.
        return self._ep_enabled
""",
    )
)


# --- 6. process_weights_after_loading: prepare and freeze the map -----------
EDITS.append(
    (
        "process_weights_after_loading",
        """    def process_weights_after_loading(self, layer: torch.nn.Module) -> None:
        self._apply_router_weight_on_input = layer.apply_router_weight_on_input
""",
        f"""    # --- {MARKER} begin: expert-map plumbing ---------------------------
    def _ep_prepare_expert_map(self, layer: torch.nn.Module) -> None:
        \"\"\"Validate and cache this rank's expert map (host-side, one-time).\"\"\"
        if not self._ep_enabled:
            return
        expert_map = getattr(layer, "expert_map", None)
        # num_local_experts is authoritative and survives the source-parameter
        # release that empties layer.w13_weight after preparation.
        local_num_experts = int(
            getattr(self.moe_config, "num_local_experts", 0)
        ) or int(layer.w13_weight.shape[0])
        expert_map = _b12x_ep_check_expert_map(
            expert_map,
            global_num_experts=self._ep_global_num_experts,
            local_num_experts=local_num_experts,
        )
        self._ep_local_num_experts = local_num_experts
        self._ep_expert_map = expert_map
        self._ep_expert_map_ptr = int(expert_map.data_ptr())
        self._ep_local_to_global = _b12x_ep_local_to_global(expert_map)

    def _ep_route_expert_map(
        self,
        expert_map: torch.Tensor | None,
    ) -> torch.Tensor | None:
        \"\"\"Return the map to bind, re-validating only when it changes.\"\"\"
        if not self._ep_enabled:
            if expert_map is not None:
                raise ValueError("b12x TP MoE does not support expert maps")
            return None
        if expert_map is None:
            raise ValueError(
                "b12x EP MoE requires an expert map; the layer supplied none"
            )
        if int(expert_map.data_ptr()) != self._ep_expert_map_ptr:
            if _is_current_stream_capturing():
                raise RuntimeError(
                    "b12x EP MoE expert map changed during CUDA graph capture"
                )
            expert_map = _b12x_ep_check_expert_map(
                expert_map,
                global_num_experts=self._ep_global_num_experts,
                local_num_experts=self._ep_local_num_experts
                or int(self.moe_config.num_local_experts),
            )
            self._ep_expert_map = expert_map
            self._ep_expert_map_ptr = int(expert_map.data_ptr())
            self._ep_local_to_global = _b12x_ep_local_to_global(expert_map)
            self._plans.clear()
        return self._ep_expert_map

    # --- {MARKER} end ---------------------------------------------------

    def process_weights_after_loading(self, layer: torch.nn.Module) -> None:
        self._apply_router_weight_on_input = layer.apply_router_weight_on_input
        self._ep_prepare_expert_map(layer)  # {MARKER}
""",
    )
)


# --- 7. _plan: route metadata spans the global expert namespace -------------
EDITS.append(
    (
        "_plan",
        """                core_token_counts=(key[0],),
                route_num_experts=0,
""",
        f"""                core_token_counts=(key[0],),
                # {MARKER}: weight_E stays local; route_E must address every
                # global router id accepted through route_expert_map.
                route_num_experts=self._ep_global_num_experts,
""",
    )
)


# --- 8. warmup: compile the mapped kernel variants --------------------------
EDITS.append(
    (
        "warmup_topk_ids",
        """            topk_ids = (
                torch.arange(topk, device=device, dtype=torch.int32)
                .unsqueeze(0)
                .expand(tokens, -1)
                .contiguous()
            )
            topk_ids.remainder_(int(prepared.num_experts))
""",
        f"""            topk_ids = (
                torch.arange(topk, device=device, dtype=torch.int32)
                .unsqueeze(0)
                .expand(tokens, -1)
                .contiguous()
            )
            topk_ids.remainder_(int(prepared.num_experts))
            if self._ep_enabled:
                # {MARKER}: with a map bound, the kernel reads GLOBAL router
                # ids.  Warm up on ids that resolve to local slots so the
                # mapped route-pack / zeroed-FC2 variants get compiled.
                assert self._ep_local_to_global is not None
                topk_ids = (
                    self._ep_local_to_global[topk_ids.to(torch.int64)]
                    .to(torch.int32)
                    .contiguous()
                )
""",
    )
)


EDITS.append(
    (
        "warmup_run",
        """            _run_b12x_moe_plan(
                plan=plan,
                scratch=scratch,
                hidden_states=hidden_states,
                prepared=prepared,
                topk_weights=topk_weights,
                topk_ids=topk_ids,
                output=output,
                unit_scale_contract=self._quant_mode == "w4a16",
            )
""",
        f"""            _run_b12x_moe_plan(
                plan=plan,
                scratch=scratch,
                hidden_states=hidden_states,
                prepared=prepared,
                topk_weights=topk_weights,
                topk_ids=topk_ids,
                output=output,
                unit_scale_contract=self._quant_mode == "w4a16",
                route_expert_map=self._ep_expert_map,  # {MARKER}
            )
""",
    )
)


# --- 9. warmup unit key: EP changes the compiled contract -------------------
EDITS.append(
    (
        "warmup_key",
        """                int(self.moe_config.experts_per_token),
                _b12x_activation_name(activation),
""",
        f"""                int(self.moe_config.experts_per_token),
                _b12x_activation_name(activation),
                self._ep_enabled,  # {MARKER}
                self._ep_global_num_experts,  # {MARKER}
""",
    )
)


# --- 10. apply: bind the map instead of rejecting it ------------------------
EDITS.append(
    (
        "apply_guard",
        """        if expert_map is not None:
            raise ValueError("b12x TP MoE does not support expert maps")
""",
        f"""        # {MARKER}: EP binds the map; TP keeps rejecting one.
        route_expert_map = self._ep_route_expert_map(expert_map)
""",
    )
)


EDITS.append(
    (
        "apply_run",
        """        _run_b12x_moe_plan(
            plan=plan,
            scratch=scratch,
            hidden_states=hidden_states,
            prepared=prepared,
            topk_weights=topk_weights,
            topk_ids=topk_ids,
            output=output,
            unit_scale_contract=self._quant_mode == "w4a16",
        )
""",
        f"""        _run_b12x_moe_plan(
            plan=plan,
            scratch=scratch,
            hidden_states=hidden_states,
            prepared=prepared,
            topk_weights=topk_weights,
            topk_ids=topk_ids,
            output=output,
            unit_scale_contract=self._quant_mode == "w4a16",
            route_expert_map=route_expert_map,  # {MARKER}
        )
""",
    )
)


def default_target() -> Path:
    import vllm  # noqa: PLC0415

    return (
        Path(vllm.__file__).parent
        / "model_executor"
        / "layers"
        / "fused_moe"
        / "b12x.py"
    )


def apply_patch(text: str) -> str:
    for name, anchor, replacement in EDITS:
        count = text.count(anchor)
        if count != 1:
            raise SystemExit(
                f"anchor {name!r} matched {count} times, expected exactly 1; "
                "the target file is not the expected fork revision"
            )
        text = text.replace(anchor, replacement, 1)
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="path to fused_moe/b12x.py (default: the installed vLLM)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write here instead of patching in place",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether the target is already patched",
    )
    args = parser.parse_args(argv)

    target = args.target or default_target()
    text = target.read_text(encoding="utf-8")

    if MARKER in text:
        print(f"already patched: {target}")
        return 0
    if args.check:
        print(f"NOT patched: {target}")
        return 1

    patched = apply_patch(text)
    destination = args.output or target
    destination.write_text(patched, encoding="utf-8")
    print(f"patched {len(EDITS)} sites -> {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
