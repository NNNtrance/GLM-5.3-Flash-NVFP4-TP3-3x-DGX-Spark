#!/usr/bin/env python3
# HAREM - FP4 crossover sweep (adversarial re-measurement of the 6 Sep MoE bench)
# Runs INSIDE harem/glm53-lil:t10. No engine, no model weights.
#
# Pre-registered design: results/kernels/fp4-crossover-sweep-gb10.md; this script implements it.
#   stage `ruler`  : three independent memory ceilings (torch sum / D2D memcpy / custom kernel)
#   stage `time`   : M-sweep, 4 arms, 2 forms, 2 routings, Layout A only
#   stage `split`  : cutlass FP4 decomposed -> GEMM-only bound + act-quant cost alone
#
# Differences vs moe_kernel_bench.py (deliberate, see the results page, "Two flaws"):
#   * per-set build+prep (peak GPU footprint ~3.5 GiB instead of 8.2 GiB)
#   * empirical byte accounting: bytes actually resident per arm, not an assumed formula
#   * b12x wrapper max_num_tokens is a knob (`matched` vs `fixed`), tonight was always >=2048
#   * ten M values instead of three, so a crossover can actually be located
import argparse, json, math, os, statistics, sys, time, types

import torch

FLOAT4_E2M1_MAX = 6.0
FLOAT8_E4M3_MAX = 448.0
QMAX = FLOAT4_E2M1_MAX * FLOAT8_E4M3_MAX  # 2688
WSIGMA = 0.25
A2_DQ = 0.0

LAYOUTS = {
    "A": dict(E_local=96, E_global=288, K=4096, N=2048, topk=8),
}


def log(*a):
    print(*a, flush=True)


def memavail_gib():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable"):
                    return round(int(ln.split()[1]) / 2**20, 2)
    except Exception:
        pass
    return -1.0


class MemGuard:
    """Abort the run if host MemAvailable drops below `floor` GiB."""

    def __init__(self, floor=6.0):
        self.floor = floor
        self.low = 99.0

    def check(self, where=""):
        m = memavail_gib()
        if 0 < m < self.low:
            self.low = m
        if 0 < m < self.floor:
            raise RuntimeError(
                f"ABORT: MemAvailable {m} GiB below floor {self.floor} GiB at {where}")
        return m


GUARD = MemGuard()


# ---------------------------------------------------------------- routing ----
def make_topk(M, topk, n_experts, routing, gen):
    if routing == "uniform":
        w = torch.ones(n_experts, device="cuda", dtype=torch.float32)
    elif routing == "zipf":
        rank = torch.arange(1, n_experts + 1, device="cuda", dtype=torch.float32)
        w = 1.0 / rank
    else:
        raise ValueError(routing)
    probs = (w / w.sum()).expand(M, n_experts).contiguous()
    ids = torch.multinomial(probs, topk, replacement=False, generator=gen)
    return ids.to(torch.int32)


def make_weights_vec(M, topk, gen):
    w = torch.rand(M, topk, device="cuda", dtype=torch.float32, generator=gen)
    return w / w.sum(dim=1, keepdim=True)


# ------------------------------------------------------------- weight bank ---
def quant_expert_bank(E, rows, K, seed, dtype=torch.bfloat16):
    from vllm import _custom_ops as ops

    g = torch.Generator(device="cuda")
    g.manual_seed(seed)
    q = torch.empty((E, rows, K // 2), dtype=torch.uint8, device="cuda")
    bs = torch.empty((E, rows, K // 16), dtype=torch.float8_e4m3fn, device="cuda")
    gs2 = torch.empty((E,), dtype=torch.float32, device="cuda")
    for e in range(E):
        w = torch.randn((rows, K), device="cuda", dtype=dtype, generator=g) * WSIGMA
        amax = w.abs().amax().to(torch.float32)
        gs_q = (QMAX / amax).reshape(1)
        qe, bse = ops.scaled_fp4_quant(w, gs_q, is_sf_swizzled_layout=False)
        q[e] = qe.view(rows, K // 2)
        bs[e] = bse.view(rows, K // 16)
        gs2[e] = 1.0 / gs_q
        del w, qe, bse
    torch.cuda.empty_cache()
    return q, bs, gs2


def build_one_set(L, i):
    w13q, w13bs, w13gs2 = quant_expert_bank(L["E_local"], 2 * L["N"], L["K"], 1000 + i)
    w2q, w2bs, w2gs2 = quant_expert_bank(L["E_local"], L["K"], L["N"], 2000 + i)
    return dict(w13q=w13q, w13bs=w13bs, w13gs2=w13gs2,
                w2q=w2q, w2bs=w2bs, w2gs2=w2gs2)


def dequant_bank(q, bs, gs2, dtype=torch.bfloat16):
    from vllm.model_executor.layers.quantization.utils.nvfp4_emulation_utils import (
        dequantize_to_dtype,
    )

    E, rows, packed = q.shape
    out = torch.empty((E, rows, packed * 2), dtype=dtype, device="cuda")
    for e in range(E):
        out[e] = dequantize_to_dtype(
            q[e], bs[e], gs2[e].reshape(1), dtype, block_size=16, swizzle=False
        )
    return out


def tensor_bytes(obj, seen=None):
    """Sum distinct storage bytes reachable from a dict/list/tensor."""
    if seen is None:
        seen = set()
    tot = 0
    if torch.is_tensor(obj):
        st = obj.untyped_storage()
        key = (st.data_ptr(), st.nbytes())
        if key not in seen:
            seen.add(key)
            tot += st.nbytes()
    elif isinstance(obj, dict):
        for v in obj.values():
            tot += tensor_bytes(v, seen)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            tot += tensor_bytes(v, seen)
    return tot


# ------------------------------------------------------------------- arms ----
class Arm:
    name = "?"
    kind = "?"
    supports_map = False
    n_sets_default = 2

    def __init__(self, L, n_sets):
        self.L = L
        self.n_sets = n_sets
        self.packs = []

    def build(self):
        """Build + prep one set at a time; never hold two raw banks."""
        for i in range(self.n_sets):
            raw = build_one_set(self.L, i)
            self.packs.append(self.prep_one(raw))
            for k in list(raw):
                raw[k] = None
            del raw
            torch.cuda.empty_cache()
            GUARD.check(f"{self.name} build set {i}")

    def prep_one(self, raw):
        raise NotImplementedError

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        raise NotImplementedError

    # measured bytes of ONE weight set as it is actually resident
    def set_bytes(self):
        return tensor_bytes(self.packs[0])

    # per-expert bytes, derived from the measured set size
    def bytes_per_expert(self):
        return self.set_bytes() / self.L["E_local"]


class BF16Arm(Arm):
    name, kind = "bf16", "bf16"
    supports_map = True
    n_sets_default = 1

    def prep_one(self, raw):
        return dict(w13=dequant_bank(raw["w13q"], raw["w13bs"], raw["w13gs2"]),
                    w2=dequant_bank(raw["w2q"], raw["w2bs"], raw["w2gs2"]))

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        from vllm.model_executor.layers.fused_moe.fused_moe import fused_experts

        p = self.packs[si]
        return fused_experts(hidden, p["w13"], p["w2"], topk_w, topk_ids,
                             global_num_experts=gne, expert_map=expert_map)


class MarlinArm(Arm):
    name, kind = "marlin-w4a16", "w4a16"
    supports_map = True

    def prep_one(self, raw):
        from vllm.model_executor.layers.quantization.utils.marlin_utils_fp4 import (
            prepare_nvfp4_moe_layer_for_marlin,
        )
        from vllm.scalar_type import scalar_types
        from vllm.model_executor.layers.quantization.utils.marlin_utils import (
            get_marlin_input_dtype,
        )

        L = self.L
        self.quant_type_id = scalar_types.float4_e2m1f.id
        stub = types.SimpleNamespace(
            num_experts=L["E_local"], hidden_size=L["K"],
            intermediate_size_per_partition=L["N"], params_dtype=torch.bfloat16)
        w13, w13s, w13s2, w2, w2s, w2s2 = prepare_nvfp4_moe_layer_for_marlin(
            layer=stub, w13=raw["w13q"],
            w13_scale=raw["w13bs"].view(torch.float8_e4m3fn),
            w13_scale_2=raw["w13gs2"], w2=raw["w2q"],
            w2_scale=raw["w2bs"].view(torch.float8_e4m3fn),
            w2_scale_2=raw["w2gs2"], is_act_and_mul=True)
        self.input_dtype = get_marlin_input_dtype(prefix="")
        return dict(w13=w13, w13s=w13s, w13s2=w13s2, w2=w2, w2s=w2s, w2s2=w2s2,
                    workspace=stub.workspace)

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        from vllm.model_executor.layers.fused_moe.experts.marlin_moe import (
            fused_marlin_moe,
        )

        p = self.packs[si]
        return fused_marlin_moe(
            hidden_states=hidden, w1=p["w13"], w2=p["w2"], bias1=None, bias2=None,
            w1_scale=p["w13s"], w2_scale=p["w2s"], topk_weights=topk_w,
            topk_ids=topk_ids, quant_type_id=self.quant_type_id,
            global_num_experts=gne, expert_map=expert_map,
            global_scale1=p["w13s2"], global_scale2=p["w2s2"],
            workspace=p["workspace"], input_dtype=self.input_dtype)

    def set_bytes(self):
        # exclude the tiny marlin workspace scratch from the weight-byte count
        p = dict(self.packs[0])
        p.pop("workspace", None)
        return tensor_bytes(p)


class CutlassFp4Arm(Arm):
    name, kind = "vllm-cutlass-w4a4", "w4a4"
    supports_map = False

    def prep_one(self, raw):
        from vllm.model_executor.layers.quantization.utils.nvfp4_utils import (
            swizzle_blockscale,
        )

        # keep references to the raw packed weights instead of cloning: build()
        # drops the raw dict's own references straight after, so nothing leaks
        # and the transient peak stays ~1.2 GiB lower.
        return dict(w13=raw["w13q"],
                    w13s=swizzle_blockscale(raw["w13bs"].view(torch.float8_e4m3fn)),
                    w13gs2=raw["w13gs2"],
                    w2=raw["w2q"],
                    w2s=swizzle_blockscale(raw["w2bs"].view(torch.float8_e4m3fn)),
                    w2gs2=raw["w2gs2"])

    def set_act_scale(self, a13_dequant, a2_dequant):
        for p in self.packs:
            p["alph13"] = (p["w13gs2"] * a13_dequant).contiguous()
            p["alph2"] = (p["w2gs2"] * a2_dequant).contiguous()
        self.ws13 = None

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        from vllm.model_executor.layers.fused_moe.experts.cutlass_moe import (
            run_cutlass_moe_fp4,
        )
        from vllm.model_executor.layers.fused_moe.activation import MoEActivation

        L = self.L
        p = self.packs[si]
        m = hidden.size(0)
        topk = topk_ids.size(1)
        need1 = (m * topk, max(2 * L["N"], L["K"]))
        need2 = (m * topk, L["N"])
        if getattr(self, "ws13", None) is None or self.ws13.shape != need1:
            self.ws13 = torch.empty(need1, dtype=torch.bfloat16, device="cuda")
            self.ws2 = torch.empty(need2, dtype=torch.bfloat16, device="cuda")
            self.out = torch.empty((m, L["K"]), dtype=torch.bfloat16, device="cuda")
        run_cutlass_moe_fp4(
            output=self.out, a=hidden, a1_gscale=self.a1_gscale, w1_fp4=p["w13"],
            w1_blockscale=p["w13s"], w1_alphas=p["alph13"], a2_gscale=self.a2_gscale,
            w2_fp4=p["w2"], w2_blockscale=p["w2s"], w2_alphas=p["alph2"],
            topk_weights=topk_w, topk_ids=topk_ids, activation=MoEActivation.SILU,
            workspace13=self.ws13, workspace2=self.ws2, m=m, n=L["N"], k=L["K"],
            e=L["E_local"], device=hidden.device)
        return self.out

    def set_bytes(self):
        p = {k: v for k, v in self.packs[0].items()
             if k in ("w13", "w13s", "w13gs2", "w2", "w2s", "w2gs2")}
        return tensor_bytes(p)

    # ------------------------------------------------ decomposition (bound) --
    def split_prepare(self, si, hidden, topk_w, topk_ids):
        """Everything run_cutlass_moe_fp4 does, hoisted out of the timed loop.

        Returns the metadata + PRE-QUANTISED activations so the two grouped
        FP4 GEMMs can be timed on their own. That pair is the floor for any
        fused custom FP4 MoE kernel: it still has to stream the same weights
        and do the same MACs, but pays no activation-quantisation, no row
        shuffle, no metadata build and no epilogue combine.
        """
        from vllm import _custom_ops as ops

        L = self.L
        p = self.packs[si]
        m = hidden.size(0)
        topk = topk_ids.size(1)
        e, n, k = L["E_local"], L["N"], L["K"]
        eo = torch.empty((e + 1), dtype=torch.int32, device="cuda")
        bo = torch.empty((e + 1), dtype=torch.int32, device="cuda")
        ps1 = torch.empty((e, 3), dtype=torch.int32, device="cuda")
        ps2 = torch.empty((e, 3), dtype=torch.int32, device="cuda")
        a_map = torch.empty((topk_ids.numel()), dtype=torch.int32, device="cuda")
        c_map = torch.empty((topk_ids.numel()), dtype=torch.int32, device="cuda")
        ops.get_cutlass_moe_mm_data(topk_ids, eo, ps1, ps2, a_map, c_map,
                                    e, n, k, bo, is_gated=True)
        a_sh = ops.shuffle_rows(hidden, a_map)
        rep_a, rep_bs = ops.scaled_fp4_experts_quant(a_sh, self.a1_gscale, eo, bo, topk)
        c1 = torch.empty((m * topk, 2 * n), dtype=torch.bfloat16, device="cuda")
        c3 = torch.empty((m * topk, k), dtype=torch.bfloat16, device="cuda")
        ops.cutlass_fp4_moe_mm(c1, rep_a, p["w13"], rep_bs, p["w13s"], p["alph13"],
                               ps1, eo[:-1], bo[:-1])
        int_a, int_bs = ops.silu_and_mul_scaled_fp4_experts_quant(
            c1, self.a2_gscale, eo, bo, topk)
        return dict(eo=eo, bo=bo, ps1=ps1, ps2=ps2, a_map=a_map, c_map=c_map,
                    a_sh=a_sh, rep_a=rep_a, rep_bs=rep_bs, c1=c1, c3=c3,
                    int_a=int_a, int_bs=int_bs, topk=topk, m=m)

    def gemm_only(self, si, sp):
        from vllm import _custom_ops as ops

        p = self.packs[si]
        ops.cutlass_fp4_moe_mm(sp["c1"], sp["rep_a"], p["w13"], sp["rep_bs"],
                               p["w13s"], p["alph13"], sp["ps1"],
                               sp["eo"][:-1], sp["bo"][:-1])
        ops.cutlass_fp4_moe_mm(sp["c3"], sp["int_a"], p["w2"], sp["int_bs"],
                               p["w2s"], p["alph2"], sp["ps2"],
                               sp["eo"][:-1], sp["bo"][:-1])

    def actquant_only(self, si, sp, hidden):
        from vllm import _custom_ops as ops

        a_sh = ops.shuffle_rows(hidden, sp["a_map"])
        ops.scaled_fp4_experts_quant(a_sh, self.a1_gscale, sp["eo"], sp["bo"],
                                     sp["topk"])
        ops.silu_and_mul_scaled_fp4_experts_quant(sp["c1"], self.a2_gscale,
                                                  sp["eo"], sp["bo"], sp["topk"])

    def meta_only(self, si, sp, topk_ids):
        """Metadata build + epilogue combine: the plumbing neither GEMM nor
        act-quant accounts for."""
        from vllm import _custom_ops as ops

        L = self.L
        e, n, k = L["E_local"], L["N"], L["K"]
        eo = torch.empty((e + 1), dtype=torch.int32, device="cuda")
        bo = torch.empty((e + 1), dtype=torch.int32, device="cuda")
        ps1 = torch.empty((e, 3), dtype=torch.int32, device="cuda")
        ps2 = torch.empty((e, 3), dtype=torch.int32, device="cuda")
        a_map = torch.empty((topk_ids.numel()), dtype=torch.int32, device="cuda")
        c_map = torch.empty((topk_ids.numel()), dtype=torch.int32, device="cuda")
        ops.get_cutlass_moe_mm_data(topk_ids, eo, ps1, ps2, a_map, c_map,
                                    e, n, k, bo, is_gated=True)
        c3 = ops.shuffle_rows(sp["c3"], sp["c_map"])
        self.out.copy_((c3.view(sp["m"], sp["topk"], k)
                        * self._tw.view(sp["m"], sp["topk"], 1).to(torch.bfloat16)
                        ).sum(dim=1), non_blocking=True)


class FlashInferB12xArm(Arm):
    name, kind = "fi-b12x-w4a4", "w4a4"
    supports_map = False
    maxtok_mode = "matched"   # "matched" | "fixed"
    maxtok_fixed = 2048

    def prep_one(self, raw):
        from vllm.model_executor.layers.quantization.utils.nvfp4_utils import (
            swizzle_blockscale,
        )
        from vllm.model_executor.layers.quantization.utils.flashinfer_fp4_moe import (
            reorder_w1w3_to_w3w1,
        )
        from vllm.utils.flashinfer import flashinfer_convert_sf_to_mma_layout as conv

        L = self.L
        w13 = raw["w13q"]
        w13bs = raw["w13bs"].view(torch.float8_e4m3fn)
        w13, w13bs = reorder_w1w3_to_w3w1(w13, w13bs)
        w13bs = (w13bs.float() * raw["w13gs2"].view(-1, 1, 1)).to(torch.float8_e4m3fn)
        w2bs = (raw["w2bs"].view(torch.float8_e4m3fn).float()
                * raw["w2gs2"].view(-1, 1, 1)).to(torch.float8_e4m3fn)
        w13bs = swizzle_blockscale(w13bs)
        w2bs = swizzle_blockscale(w2bs)
        e1, m1, ksf1 = w13bs.shape
        sf1 = conv(w13bs.reshape(e1 * m1, ksf1), m=m1, k=ksf1 * 16, num_groups=e1)
        e2, m2, ksf2 = w2bs.shape
        sf2 = conv(w2bs.reshape(e2 * m2, ksf2), m=m2, k=ksf2 * 16, num_groups=e2)
        ones = torch.ones(L["E_local"], device="cuda", dtype=torch.float32)
        # w13 already comes out of reorder_w1w3_to_w3w1 as a fresh tensor;
        # w2 is referenced, not cloned (build() drops the raw dict right after).
        pack = dict(w13=w13, sf1=sf1, w2=raw["w2q"], sf2=sf2,
                    a1=ones, a2=ones.clone())
        del w13bs, w2bs
        torch.cuda.empty_cache()
        self.wrapper = None
        self.built_key = None
        return pack

    def reset_wrapper(self):
        self.wrapper = None
        self.built_key = None

    def _ensure(self, M, topk):
        from flashinfer.fused_moe import B12xMoEWrapper

        L = self.L
        if self.maxtok_mode == "matched":
            want = max(8, int(M))
        else:
            want = max(int(M), self.maxtok_fixed)
        key = (want, topk)
        if self.wrapper is None or key != self.built_key:
            self.built_key = key
            self.wrapper = B12xMoEWrapper(
                num_experts=L["E_local"], top_k=topk, hidden_size=L["K"],
                intermediate_size=L["N"], use_cuda_graph=True,
                max_num_tokens=want, num_local_experts=L["E_local"],
                activation="silu")

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        p = self.packs[si]
        self._ensure(hidden.size(0), topk_ids.size(1))
        return self.wrapper.run(
            x=hidden, w1_weight=p["w13"], w1_weight_sf=p["sf1"], w1_alpha=p["a1"],
            fc2_input_scale=p["a2"], w2_weight=p["w2"], w2_weight_sf=p["sf2"],
            w2_alpha=p["a2"], token_selected_experts=topk_ids.to(torch.int32),
            token_final_scales=topk_w)

    def set_bytes(self):
        p = {k: v for k, v in self.packs[0].items()
             if k in ("w13", "sf1", "w2", "sf2")}
        return tensor_bytes(p)


ARMS = {c.name: c for c in [BF16Arm, MarlinArm, CutlassFp4Arm, FlashInferB12xArm]}


# ------------------------------------------------------------------ rulers ---
CUSTOM_SRC = r"""
#include <torch/extension.h>
#include <cuda_runtime.h>

__global__ void harem_stream_read(const uint4* __restrict__ p,
                                  unsigned long long n4,
                                  unsigned int* __restrict__ sink) {
    unsigned long long i = (unsigned long long)blockIdx.x * blockDim.x + threadIdx.x;
    unsigned long long stride = (unsigned long long)gridDim.x * blockDim.x;
    unsigned int a = 0u, b = 0u, c = 0u, d = 0u;
    for (; i < n4; i += stride) {
        uint4 v = __ldg(&p[i]);
        a ^= v.x; b ^= v.y; c ^= v.z; d ^= v.w;
    }
    unsigned int s = a ^ b ^ c ^ d;
    // never taken in practice, but the compiler cannot prove it -> loads stay
    if (s == 0xDEADBEEFu) atomicAdd(sink, 1u);
}

void harem_stream_read_launch(at::Tensor buf, at::Tensor sink, int64_t blocks,
                              int64_t threads) {
    unsigned long long n4 = (unsigned long long)(buf.nbytes() / 16);
    harem_stream_read<<<(int)blocks, (int)threads>>>(
        reinterpret_cast<const uint4*>(buf.data_ptr()), n4,
        reinterpret_cast<unsigned int*>(sink.data_ptr()));
}
"""

CUSTOM_DECL = ("void harem_stream_read_launch(at::Tensor buf, at::Tensor sink, "
               "int64_t blocks, int64_t threads);")


def _time_ms(fn, warm=5, iters=20):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    best = []
    for _ in range(3):
        st = torch.cuda.Event(True); en = torch.cuda.Event(True)
        st.record()
        for _ in range(iters):
            fn()
        en.record(); torch.cuda.synchronize()
        best.append(st.elapsed_time(en) / iters)
    return best


def ruler_torch_small():
    """Exactly tonight's ruler: 4 x 32 MiB bf16 buffers, torch .sum()."""
    bufs = [torch.randn(512, 32768, dtype=torch.bfloat16, device="cuda")
            for _ in range(4)]
    nbytes = bufs[0].numel() * 2
    k = {"i": 0}

    def f():
        bufs[k["i"] % 4].sum()
        k["i"] += 1

    ms = _time_ms(f, warm=20, iters=200)
    del bufs
    torch.cuda.empty_cache()
    return dict(method="torch .sum(), 4 x 32 MiB bf16 (tonight's ruler)",
                buf_mib=nbytes // 2**20, rounds_gbps=[round(nbytes / (x * 1e-3) / 1e9, 1) for x in ms],
                gbps=round(nbytes / (statistics.median(ms) * 1e-3) / 1e9, 1))


def ruler_torch_big(gib):
    """Same method, one big buffer, so 'size' is separated from 'method'."""
    n = int(gib * 2**30) // 2
    buf = torch.empty(n, dtype=torch.bfloat16, device="cuda").normal_()
    nbytes = n * 2
    ms = _time_ms(lambda: buf.sum(), warm=5, iters=20)
    del buf
    torch.cuda.empty_cache()
    return dict(method=f"torch .sum(), one {gib} GiB bf16 buffer",
                buf_mib=nbytes // 2**20,
                rounds_gbps=[round(nbytes / (x * 1e-3) / 1e9, 1) for x in ms],
                gbps=round(nbytes / (statistics.median(ms) * 1e-3) / 1e9, 1))


def ruler_memcpy(gib):
    """cudaMemcpyDeviceToDevice. Moves `gib` in and `gib` out; the
    read-equivalent ceiling is total DRAM traffic / time."""
    n = int(gib * 2**30)
    src = torch.empty(n, dtype=torch.uint8, device="cuda")
    dst = torch.empty(n, dtype=torch.uint8, device="cuda")
    src.random_(0, 255)
    ms = _time_ms(lambda: dst.copy_(src, non_blocking=True), warm=5, iters=20)
    med = statistics.median(ms)
    del src, dst
    torch.cuda.empty_cache()
    return dict(method=f"cudaMemcpyDeviceToDevice, {gib} GiB buffer",
                buf_mib=n // 2**20,
                copy_gbps=round(n / (med * 1e-3) / 1e9, 1),
                rounds_gbps=[round(2 * n / (x * 1e-3) / 1e9, 1) for x in ms],
                gbps=round(2 * n / (med * 1e-3) / 1e9, 1),
                note="gbps = (read+write)/t = read-equivalent traffic")


def ruler_custom(gib):
    """Minimal hand-written streaming-read kernel: one pass, __ldg, 16-byte
    vector loads, XOR reduction."""
    from torch.utils.cpp_extension import load_inline

    os.environ.setdefault("TORCH_EXTENSIONS_DIR", "/bench/torchext")
    os.environ.setdefault("TORCH_CUDA_ARCH_LIST", "12.1")
    os.makedirs(os.environ["TORCH_EXTENSIONS_DIR"], exist_ok=True)
    mod = load_inline(
        name="harem_ruler", cpp_sources=CUSTOM_DECL, cuda_sources=CUSTOM_SRC,
        functions=["harem_stream_read_launch"], verbose=False,
        extra_cuda_cflags=["-O3", "--use_fast_math", "-gencode",
                           "arch=compute_121,code=sm_121"])
    n = int(gib * 2**30)
    buf = torch.empty(n, dtype=torch.uint8, device="cuda")
    buf.random_(0, 255)
    sink = torch.zeros(1, dtype=torch.int32, device="cuda")
    sm = torch.cuda.get_device_properties(0).multi_processor_count
    best = None
    for threads in (256, 512):
        for per_sm in (2, 4, 8, 16):
            blocks = sm * per_sm
            ms = _time_ms(
                lambda b=blocks, t=threads: mod.harem_stream_read_launch(
                    buf, sink, b, t), warm=5, iters=20)
            med = statistics.median(ms)
            g = n / (med * 1e-3) / 1e9
            if best is None or g > best["gbps"]:
                best = dict(gbps=round(g, 1), threads=threads, blocks=blocks,
                            rounds_gbps=[round(n / (x * 1e-3) / 1e9, 1) for x in ms])
    del buf, sink
    torch.cuda.empty_cache()
    best["method"] = f"custom __ldg/uint4 streaming read, {gib} GiB"
    best["buf_mib"] = n // 2**20
    return best


def cmd_ruler(args):
    out = {"device": torch.cuda.get_device_name(0),
           "sm_count": torch.cuda.get_device_properties(0).multi_processor_count,
           "capability": list(torch.cuda.get_device_capability(0)),
           "spec_lpddr5x_gbps": 273.0,
           "memavail_start_gib": memavail_gib(), "rulers": {}}
    gib = args.ruler_gib

    def shrinking(f):
        """Retry with a halved buffer if the box cannot spare the memory."""
        def g():
            last = None
            for factor in (1.0, 0.5, 0.25):
                try:
                    return f(gib * factor)
                except (torch.cuda.OutOfMemoryError, RuntimeError) as e:
                    if "out of memory" not in str(e).lower():
                        raise
                    last = e
                    torch.cuda.empty_cache()
            raise last
        return g

    steps = [("a_torch_sum_small", lambda: ruler_torch_small()),
             ("a2_torch_sum_big", shrinking(ruler_torch_big)),
             ("b_memcpy_d2d", shrinking(ruler_memcpy)),
             ("c_custom_stream_read", shrinking(ruler_custom))]
    for nm, fn in steps:
        try:
            out["rulers"][nm] = fn()
            log(f"  {nm:24s} -> {out['rulers'][nm]['gbps']} GB/s")
        except Exception as e:
            import traceback
            out["rulers"][nm] = {"status": "FAILED", "error": f"{type(e).__name__}: {e}",
                                 "tb": traceback.format_exc()[-1200:]}
            log(f"  {nm:24s} -> FAILED {e}")
        GUARD.check("ruler " + nm)
        torch.cuda.empty_cache()
    ok = [v["gbps"] for v in out["rulers"].values() if "gbps" in v]
    out["ceiling_gbps"] = max(ok) if ok else None
    out["memavail_min_gib"] = GUARD.low
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    log("CEILING = %s GB/s   WROTE %s" % (out["ceiling_gbps"], args.out))


# ------------------------------------------------------------------ timing ---
def time_call(fn, warmup, iters, rounds):
    for i in range(warmup):
        fn(i)
    torch.cuda.synchronize()
    res = []
    for r in range(rounds):
        st = torch.cuda.Event(True); en = torch.cuda.Event(True)
        st.record()
        for i in range(iters):
            fn(i)
        en.record(); torch.cuda.synchronize()
        res.append(st.elapsed_time(en) / iters * 1000.0)  # us
    return res


def flush(out, path):
    """Write the JSON after every point, so a watchdog kill at a later, bigger
    M does not throw away everything measured before it."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, path)


def make_problem(L, form, M, routing, seed):
    gen = torch.Generator(device="cuda"); gen.manual_seed(seed)
    K = L["K"]
    if form == "dense":
        ids = make_topk(M, L["topk"], L["E_local"], routing, gen)
        w = make_weights_vec(M, L["topk"], gen)
        hid = (torch.randn(M, K, device="cuda", dtype=torch.bfloat16, generator=gen)
               * 0.5).contiguous()
        return hid, w, ids, None, L["E_local"], M * L["topk"], torch.unique(ids), M
    if form == "epcompact":
        ids = make_topk(M, L["topk"], L["E_global"], routing, gen)
        w = make_weights_vec(M, L["topk"], gen)
        mask = ids < L["E_local"]
        rows = torch.nonzero(mask, as_tuple=False)
        R = rows.size(0)
        lid = ids[mask].reshape(R, 1).contiguous()
        lw = w[mask].reshape(R, 1).contiguous()
        hid_full = (torch.randn(M, K, device="cuda", dtype=torch.bfloat16,
                                generator=gen) * 0.5)
        hid = hid_full[rows[:, 0]].contiguous()
        del hid_full
        return hid, lw, lid, None, L["E_local"], R, torch.unique(lid), R
    raise ValueError(form)


def _act_scales(hidden, E):
    amax = hidden.abs().amax().to(torch.float32)
    return (amax / QMAX).repeat(E).contiguous()


def cmd_time(args):
    L = dict(LAYOUTS["A"])
    cls = ARMS[args.arm]
    n_sets = args.sets if args.sets > 0 else cls.n_sets_default
    if args.arm == "fi-b12x-w4a4":
        cls.maxtok_mode = args.b12x_maxtok
    arm = cls(L, n_sets)
    t0 = time.time()
    arm.build()
    sb = arm.set_bytes()
    out = {"layout": "A", "arm": args.arm, "tag": args.tag, "sets": n_sets,
           "shapes": L, "build_s": round(time.time() - t0, 1),
           "set_bytes_measured": int(sb),
           "bytes_per_expert_measured": int(sb // L["E_local"]),
           "b12x_maxtok_mode": getattr(cls, "maxtok_mode", None),
           "memavail_start_gib": memavail_gib(),
           "mem_after_prep": round(torch.cuda.memory_allocated() / 2**30, 2),
           "runs": []}
    log(f"  set_bytes={sb} ({sb/2**30:.3f} GiB)  per-expert={sb//L['E_local']} B")
    for form in args.forms:
        for M in args.M:
            for routing in args.routing:
                try:
                    hid, w, ids, emap, gne, pairs, used, rows = make_problem(
                        L, form, M, routing, 11 + M)
                    if isinstance(arm, CutlassFp4Arm):
                        a13 = _act_scales(hid, L["E_local"])
                        a2 = (torch.full_like(a13, A2_DQ) if A2_DQ else a13.clone())
                        arm.set_act_scale(a13, a2)
                        arm.a1_gscale = (1.0 / a13).contiguous()
                        arm.a2_gscale = (1.0 / a2).contiguous()
                        arm.ws13 = None
                    if isinstance(arm, FlashInferB12xArm):
                        arm.reset_wrapper()
                    fn = lambda i: arm.call(i % n_sets, hid, w, ids, emap, gne)
                    us = time_call(fn, args.warmup, args.iters, args.rounds)
                    nexp = int(used.numel())
                    tb = nexp * (sb / L["E_local"])
                    flops = pairs * 6.0 * L["N"] * L["K"]
                    med = statistics.median(us)
                    rec = dict(form=form, M=M, routing=routing, rows=int(rows),
                               pairs=int(pairs), experts_touched=nexp,
                               us_rounds=[round(x, 2) for x in us], us=round(med, 2),
                               spread_pct=round((max(us) - min(us)) / med * 100, 2),
                               weight_bytes=int(tb), gflop=round(flops / 1e9, 2),
                               gbps=round(tb / (med * 1e-6) / 1e9, 1),
                               tflops=round(flops / (med * 1e-6) / 1e12, 2),
                               b12x_maxtok=getattr(arm, "built_key", None))
                    out["runs"].append(rec)
                    log(f"  {form:10s} M={M:5d} {routing:8s} rows={rows:6d} "
                        f"exp={nexp:3d} -> {med:9.2f} us  {rec['gbps']:6.1f} GB/s "
                        f"{rec['tflops']:6.2f} TF")
                    del hid, w, ids
                    torch.cuda.empty_cache()
                    out["memavail_min_gib"] = GUARD.low
                    out["mem_peak_gib"] = round(
                        torch.cuda.max_memory_allocated() / 2**30, 2)
                    flush(out, args.out)
                    GUARD.check(f"{args.arm} {form} M={M} {routing}")
                except Exception as e:
                    import traceback
                    out["runs"].append(dict(form=form, M=M, routing=routing,
                                            status="FAILED",
                                            error=f"{type(e).__name__}: {e}",
                                            tb=traceback.format_exc()[-1000:]))
                    log(f"  {form:10s} M={M:5d} {routing:8s} -> FAILED {e}")
                    torch.cuda.empty_cache()
    out["mem_peak_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    out["memavail_min_gib"] = GUARD.low
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    log("WROTE " + args.out)


# ---------------------------------------------------- cutlass decomposition --
def cmd_split(args):
    """GEMM-only bound + act-quant cost alone + plumbing, for the cutlass FP4
    path. b12x has no pre-quantised entry point in its public API, so the
    cutlass grouped-GEMM pair is used as the FP4 GEMM floor."""
    L = dict(LAYOUTS["A"])
    arm = CutlassFp4Arm(L, args.sets if args.sets > 0 else 2)
    t0 = time.time()
    arm.build()
    sb = arm.set_bytes()
    out = {"layout": "A", "arm": "vllm-cutlass-w4a4-split", "sets": arm.n_sets,
           "shapes": L, "build_s": round(time.time() - t0, 1),
           "set_bytes_measured": int(sb),
           "bytes_per_expert_measured": int(sb // L["E_local"]),
           "memavail_start_gib": memavail_gib(), "runs": []}
    for form in args.forms:
        for M in args.M:
            for routing in args.routing:
                try:
                    hid, w, ids, emap, gne, pairs, used, rows = make_problem(
                        L, form, M, routing, 11 + M)
                    a13 = _act_scales(hid, L["E_local"])
                    a2 = (torch.full_like(a13, A2_DQ) if A2_DQ else a13.clone())
                    arm.set_act_scale(a13, a2)
                    arm.a1_gscale = (1.0 / a13).contiguous()
                    arm.a2_gscale = (1.0 / a2).contiguous()
                    arm.ws13 = None
                    arm._tw = w
                    # full path once, to allocate and to have a reference time
                    arm.call(0, hid, w, ids, None, gne)
                    # ONE activation/metadata workspace, shared by both weight
                    # sets: only the weights need to rotate for the L2 argument,
                    # and duplicating c1/c3 would double the footprint at M=4096.
                    sp = arm.split_prepare(0, hid, w, ids)
                    torch.cuda.synchronize()

                    full = time_call(lambda i: arm.call(i % arm.n_sets, hid, w, ids,
                                                        None, gne),
                                     args.warmup, args.iters, args.rounds)
                    gemm = time_call(lambda i: arm.gemm_only(i % arm.n_sets, sp),
                                     args.warmup, args.iters, args.rounds)
                    aq = time_call(lambda i: arm.actquant_only(i % arm.n_sets,
                                                               sp, hid),
                                   args.warmup, args.iters, args.rounds)
                    mt = time_call(lambda i: arm.meta_only(i % arm.n_sets, sp, ids),
                                   args.warmup, args.iters, args.rounds)
                    nexp = int(used.numel())
                    tb = nexp * (sb / L["E_local"])
                    flops = pairs * 6.0 * L["N"] * L["K"]
                    mf, mg, ma, mm = (statistics.median(x) for x in (full, gemm, aq, mt))
                    rec = dict(form=form, M=M, routing=routing, rows=int(rows),
                               pairs=int(pairs), experts_touched=nexp,
                               us_full=round(mf, 2), us_gemm_only=round(mg, 2),
                               us_actquant=round(ma, 2), us_meta_combine=round(mm, 2),
                               us_residual=round(mf - mg - ma - mm, 2),
                               weight_bytes=int(tb), gflop=round(flops / 1e9, 2),
                               gemm_gbps=round(tb / (mg * 1e-6) / 1e9, 1),
                               gemm_tflops=round(flops / (mg * 1e-6) / 1e12, 2))
                    out["runs"].append(rec)
                    log(f"  {form:10s} M={M:5d} {routing:8s} -> full {mf:9.2f}  "
                        f"gemm {mg:9.2f}  actq {ma:8.2f}  meta {mm:8.2f} us")
                    sp.clear()
                    del hid, w, ids, sp
                    torch.cuda.empty_cache()
                    out["memavail_min_gib"] = GUARD.low
                    out["mem_peak_gib"] = round(
                        torch.cuda.max_memory_allocated() / 2**30, 2)
                    flush(out, args.out)
                    GUARD.check(f"split {form} M={M} {routing}")
                except Exception as e:
                    import traceback
                    out["runs"].append(dict(form=form, M=M, routing=routing,
                                            status="FAILED",
                                            error=f"{type(e).__name__}: {e}",
                                            tb=traceback.format_exc()[-1000:]))
                    log(f"  {form:10s} M={M:5d} {routing:8s} -> FAILED {e}")
                    torch.cuda.empty_cache()
    out["mem_peak_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 2)
    out["memavail_min_gib"] = GUARD.low
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    log("WROTE " + args.out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["ruler", "time", "split"])
    p.add_argument("--arm", default="marlin-w4a16")
    p.add_argument("--tag", default="")
    p.add_argument("--M", nargs="+", type=int,
                   default=[8, 16, 32, 64, 128, 256, 512, 1024, 1792, 4096])
    p.add_argument("--routing", nargs="+", default=["uniform", "zipf"])
    p.add_argument("--forms", nargs="+", default=["dense", "epcompact"])
    p.add_argument("--sets", type=int, default=0)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--iters", type=int, default=50)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--a2dq", type=float, default=0.0)
    p.add_argument("--ruler-gib", type=float, default=2.0)
    p.add_argument("--mem-floor", type=float, default=6.0)
    p.add_argument("--b12x-maxtok", default="matched", choices=["matched", "fixed"])
    p.add_argument("--out", default="/bench/out.json")
    args = p.parse_args()
    globals()["A2_DQ"] = args.a2dq
    GUARD.floor = args.mem_floor
    torch.cuda.init()
    from vllm.config import VllmConfig, set_current_vllm_config
    try:
        ctx = set_current_vllm_config(VllmConfig())
    except Exception as e:
        log("WARN: no VllmConfig context (%s)" % e)
        import contextlib
        ctx = contextlib.nullcontext()
    with ctx:
        {"ruler": cmd_ruler, "time": cmd_time, "split": cmd_split}[args.mode](args)


if __name__ == "__main__":
    main()
