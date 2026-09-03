#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CPU unit tests for the b12x MoE expert-parallel id-remap / mask logic.

No GPU, no vLLM import: the three helpers the patch adds to
``vllm/model_executor/layers/fused_moe/b12x.py`` are extracted from the
patched source by text and exec'd against plain torch.  That keeps the test
honest -- it exercises the code that will actually ship -- while running
anywhere torch imports.

Run:
    python3 test_b12x_moe_ep_cpu.py [--source /path/to/b12x.patched.py]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import torch

MARKER_BEGIN = "# --- HAREM-B12X-MOE-EP begin: expert-parallel helpers"
MARKER_END = "# --- HAREM-B12X-MOE-EP end"


# ---------------------------------------------------------------------------
# load the helpers out of the patched backend
# ---------------------------------------------------------------------------


def load_helpers(source: Path) -> dict:
    text = source.read_text(encoding="utf-8")
    start = text.index(MARKER_BEGIN)
    end = text.index(MARKER_END, start)
    block = text[start:end]
    namespace: dict = {
        "torch": torch,
        "Any": object,
        # the recipe table the helper indexes into
        "_B12X_MOE_MODES": {
            ("mxfp4", "mxfp8"): ("w4a8_mx", "fp4_e8m0_k32", "w31"),
            ("mxfp4", None): ("w4a16", "fp4_e8m0_k32", "w31"),
            ("nvfp4", "nvfp4"): ("nvfp4", "modelopt_nvfp4", "w31"),
            ("nvfp4", "mxfp8"): ("w4a8_nvfp4", "modelopt_nvfp4", "w31"),
            ("nvfp4", None): ("w4a16", "modelopt_nvfp4", "w13"),
        },
    }
    exec(compile(block, str(source), "exec"), namespace)  # noqa: S102
    for name in (
        "_b12x_ep_w4a16_mode",
        "_b12x_ep_check_expert_map",
        "_b12x_ep_local_to_global",
        "_b12x_ep_map_topk_to_local",
    ):
        if name not in namespace:
            raise SystemExit(f"helper {name} missing from {source}")
    return namespace


# ---------------------------------------------------------------------------
# reference expert maps (mirrors determine_expert_map in expert_map_manager)
# ---------------------------------------------------------------------------


def linear_expert_map(ep_size: int, ep_rank: int, global_e: int) -> torch.Tensor:
    base, remainder = divmod(global_e, ep_size)
    local_e = base + 1 if ep_rank < remainder else base
    expert_map = torch.full((global_e,), -1, dtype=torch.int32)
    start = ep_rank * base + min(ep_rank, remainder)
    expert_map[start : start + local_e] = torch.arange(local_e, dtype=torch.int32)
    return expert_map


def round_robin_expert_map(ep_size: int, ep_rank: int, global_e: int) -> torch.Tensor:
    expert_map = torch.full((global_e,), -1, dtype=torch.int32)
    owned = torch.arange(ep_rank, global_e, ep_size, dtype=torch.int64)
    expert_map[owned] = torch.arange(owned.numel(), dtype=torch.int32)
    return expert_map


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

GLOBAL_E = 288
EP_SIZE = 3
TOP_K = 8
HIDDEN = 32
INTERMEDIATE = 16


def test_recipe_selection(h: dict) -> None:
    assert h["_b12x_ep_w4a16_mode"]("nvfp4") == ("w4a16", "modelopt_nvfp4", "w13")
    assert h["_b12x_ep_w4a16_mode"]("mxfp4") == ("w4a16", "fp4_e8m0_k32", "w31")
    try:
        h["_b12x_ep_w4a16_mode"]("int4")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown weight format must be rejected")


def test_map_validation(h: dict) -> None:
    check = h["_b12x_ep_check_expert_map"]
    for rank in range(EP_SIZE):
        emap = linear_expert_map(EP_SIZE, rank, GLOBAL_E)
        local_e = int((emap >= 0).sum())
        assert local_e == GLOBAL_E // EP_SIZE == 96
        assert check(emap, global_num_experts=GLOBAL_E, local_num_experts=local_e) is emap
        rr = round_robin_expert_map(EP_SIZE, rank, GLOBAL_E)
        assert check(rr, global_num_experts=GLOBAL_E, local_num_experts=local_e) is rr

    good = linear_expert_map(EP_SIZE, 0, GLOBAL_E)
    bad_cases = [
        ("None", None, GLOBAL_E, 96),
        ("dtype", good.to(torch.int64), GLOBAL_E, 96),
        ("rank", good.view(2, -1), GLOBAL_E, 96),
        ("length", good[:-1].contiguous(), GLOBAL_E, 96),
        ("global mismatch", good, GLOBAL_E - 1, 96),
        ("local too big", good, GLOBAL_E, 97),
        ("local zero", good, GLOBAL_E, 0),
    ]
    for name, emap, g, l in bad_cases:
        try:
            check(emap, global_num_experts=g, local_num_experts=l)
        except (TypeError, ValueError):
            continue
        raise AssertionError(f"malformed map accepted: {name}")

    # a duplicated local slot must be rejected (would index the wrong weights)
    duped = linear_expert_map(EP_SIZE, 0, GLOBAL_E).clone()
    duped[5] = duped[4]
    try:
        check(duped, global_num_experts=GLOBAL_E, local_num_experts=96)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate local slot accepted")

    # an out-of-range local id must be rejected
    oob = linear_expert_map(EP_SIZE, 0, GLOBAL_E).clone()
    oob[7] = 96
    try:
        check(oob, global_num_experts=GLOBAL_E, local_num_experts=96)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range local id accepted")


def test_local_to_global(h: dict) -> None:
    for maker in (linear_expert_map, round_robin_expert_map):
        for rank in range(EP_SIZE):
            emap = maker(EP_SIZE, rank, GLOBAL_E)
            l2g = h["_b12x_ep_local_to_global"](emap)
            assert l2g.dtype == torch.int32
            assert int(l2g.numel()) == int((emap >= 0).sum())
            # round trip: expert_map[l2g[i]] == i for every local slot i
            back = emap[l2g.to(torch.int64)]
            assert torch.equal(
                back, torch.arange(l2g.numel(), dtype=torch.int32)
            ), f"{maker.__name__} rank {rank} round trip failed"


def test_topk_remap_and_mask(h: dict) -> None:
    torch.manual_seed(0)
    tokens = 64
    topk_ids = torch.randint(0, GLOBAL_E, (tokens, TOP_K), dtype=torch.int32)
    covered = torch.zeros_like(topk_ids, dtype=torch.bool)
    for rank in range(EP_SIZE):
        emap = linear_expert_map(EP_SIZE, rank, GLOBAL_E)
        local = h["_b12x_ep_map_topk_to_local"](topk_ids, emap)
        assert local.dtype == torch.int32
        owned = (topk_ids >= rank * 96) & (topk_ids < (rank + 1) * 96)
        # -1 exactly on remote routes
        assert torch.equal(local >= 0, owned)
        # local ids are the global id minus the rank's base offset
        assert torch.equal(
            local[owned].to(torch.int64),
            topk_ids[owned].to(torch.int64) - rank * 96,
        )
        assert int(local[owned].max()) < 96
        # each route is owned by exactly one rank
        assert not bool((covered & owned).any())
        covered |= owned
    assert bool(covered.all()), "some routes were owned by no rank"


def reference_moe(
    x: torch.Tensor,
    w13: torch.Tensor,
    w2: torch.Tensor,
    topk_ids: torch.Tensor,
    topk_weights: torch.Tensor,
    *,
    local_ids: torch.Tensor | None = None,
) -> torch.Tensor:
    """SiLU-gated MoE over whatever routes survive ``local_ids``.

    ``local_ids`` is the per-route local expert id (-1 = drop), i.e. exactly
    what the b12x route-pack produces from ``route_expert_map``.  When it is
    None every route is taken (the TP / single-rank reference).
    """
    tokens = x.shape[0]
    out = torch.zeros(tokens, w2.shape[1], dtype=x.dtype)
    for t in range(tokens):
        for slot in range(topk_ids.shape[1]):
            if local_ids is None:
                expert = int(topk_ids[t, slot])
            else:
                expert = int(local_ids[t, slot])
                if expert < 0:
                    continue  # remote route: another rank owns it
            h = x[t] @ w13[expert].t()
            gate, up = h.chunk(2, dim=-1)
            act = torch.nn.functional.silu(gate) * up
            out[t] += float(topk_weights[t, slot]) * (act @ w2[expert].t())
    return out


def test_ep_partials_sum_to_tp_result(h: dict) -> None:
    """The load-bearing correctness claim.

    sum over ranks of (rank-local MoE with remote routes dropped)
        == the single-rank MoE over all 288 experts

    which is what the framework's tensor_model_parallel_all_reduce does with
    the per-rank partial that ``B12xExperts.apply`` now produces.
    """
    torch.manual_seed(7)
    global_e = 24  # small, same structure: divisible by ep_size
    ep_size = 3
    local_e = global_e // ep_size
    tokens, topk = 11, 4

    x = torch.randn(tokens, HIDDEN, dtype=torch.float64)
    w13 = torch.randn(global_e, 2 * INTERMEDIATE, HIDDEN, dtype=torch.float64) * 0.1
    w2 = torch.randn(global_e, HIDDEN, INTERMEDIATE, dtype=torch.float64) * 0.1

    topk_ids = torch.stack(
        [torch.randperm(global_e)[:topk] for _ in range(tokens)]
    ).to(torch.int32)
    topk_weights = torch.softmax(torch.randn(tokens, topk, dtype=torch.float64), -1)

    full = reference_moe(x, w13, w2, topk_ids, topk_weights)

    for maker in (linear_expert_map, round_robin_expert_map):
        accumulated = torch.zeros_like(full)
        for rank in range(ep_size):
            emap = maker(ep_size, rank, global_e)
            h["_b12x_ep_check_expert_map"](
                emap, global_num_experts=global_e, local_num_experts=local_e
            )
            local_ids = h["_b12x_ep_map_topk_to_local"](topk_ids, emap)
            l2g = h["_b12x_ep_local_to_global"](emap).to(torch.int64)
            # this rank holds only its own experts, in local-slot order --
            # exactly how the vLLM weight loader fills w13/w2 under EP
            rank_w13 = w13[l2g]
            rank_w2 = w2[l2g]
            partial = reference_moe(
                x, rank_w13, rank_w2, topk_ids, topk_weights, local_ids=local_ids
            )
            accumulated += partial
        err = (accumulated - full).abs().max().item()
        assert err < 1e-12, f"{maker.__name__}: EP partials != TP result ({err})"


def test_dropped_tokens_stay_zero(h: dict) -> None:
    """A token whose every route is remote must contribute an all-zero row."""
    global_e, ep_size, local_e = 24, 3, 8
    emap = linear_expert_map(ep_size, 0, global_e)  # owns globals 0..7
    topk_ids = torch.tensor([[8, 9, 10, 11], [0, 9, 1, 23]], dtype=torch.int32)
    local_ids = h["_b12x_ep_map_topk_to_local"](topk_ids, emap)
    assert torch.equal(
        local_ids,
        torch.tensor([[-1, -1, -1, -1], [0, -1, 1, -1]], dtype=torch.int32),
    )
    assert int((local_ids[0] >= 0).sum()) == 0
    assert int((local_ids[1] >= 0).sum()) == 2
    assert local_e == int((emap >= 0).sum())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).with_name("b12x.patched.py"),
        help="patched b12x.py to extract the helpers from",
    )
    args = parser.parse_args(argv)

    helpers = load_helpers(args.source)
    tests = [
        test_recipe_selection,
        test_map_validation,
        test_local_to_global,
        test_topk_remap_and_mask,
        test_ep_partials_sum_to_tp_result,
        test_dropped_tokens_stay_zero,
    ]
    failures = 0
    for test in tests:
        try:
            test(helpers)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
