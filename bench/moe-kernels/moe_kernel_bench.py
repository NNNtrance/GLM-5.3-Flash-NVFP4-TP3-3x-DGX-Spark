#!/usr/bin/env python3
"""Model-free MoE kernel bench for GLM-5.3-Flash NVFP4 on GB10 (sm_121).

Runs INSIDE the production image (harem/glm53-lil:t10). No engine, no model
weights, no checkpoint: synthetic NVFP4 expert banks are built in the
*checkpoint* layout with the image's own `scaled_fp4_quant`, handed to each
backend's own repack helper, and then the MoE kernels are called directly.

Question it answers: at the production MoE shapes, what does the weight-only
marlin W4A16 path cost against the FP4 tensor-core paths the chip has?

Three modes:

  probe   device, vLLM/FlashInfer versions, which FP4 backends accept this
          device, and the bandwidth ruler.
  gate    correctness. Identical inputs, one dequantised weight bank, full
          production K/N/topk on a reduced expert bank. Two references:
          bf16-of-dequantised-weights (for W4A16 arms) and *emulated W4A4*
          (activations quantise-dequantised at both GEMM inputs, for W4A4
          arms). A W4A4 kernel cannot reach cosine 0.99 against bf16 - the
          emulated W4A4 reference itself does not - so W4A4 arms are gated
          against the emulated reference. Read the VERDICT field with that
          in mind; the write-up explains where it is mechanically wrong.
  time    the timing sweep. CUDA events, `--warmup` calls, then `--rounds`
          rounds of `--iters` calls; the round median is reported, with the
          round-to-round spread. Two weight sets are rotated per call so a
          24 MiB L2 cannot hold the working set (`--sets 1` for bf16, which
          reads 2.4-4.8 GiB per call anyway and would not fit twice).

Layouts (`--layout`):
  A   production today: TP=3 + expert parallelism, 96 local experts of 288,
      K=4096, N=2048 (gate/up 2x2048), top-8.
  B   the no-EP candidate: MoE intermediate padded 2048 -> 2304 and sliced
      by 3 = 768 per rank, all 288 experts local, K=4096, top-8.

Forms (`--forms`), i.e. how expert parallelism is modelled on layout A:
  dense       "all 8 local": top-8 over the 96 local experts, M rows.
  epcompact   EP-effective traffic: route top-8 over 288, drop the non-local
              pairs, run the surviving (token, expert) pairs as topk=1 over
              96. This is what a rank actually sees. Every arm can run it.
  nativeep    what production runs: global ids over 288 plus an expert_map
              with -1 for non-local. Only marlin and bf16 accept expert maps;
              the FP4 paths refuse them. `nativeep` and `epcompact` were
              measured equivalent on marlin, which is what makes `epcompact`
              a fair proxy for the FP4 arms.

Arms (`--arm` / `--arms`):
  marlin-w4a16       fused_marlin_moe, the production path (weight-only)
  vllm-cutlass-w4a4  run_cutlass_moe_fp4, activations quantised in the
                     timed region
  fi-b12x-w4a4       flashinfer.fused_moe.B12xMoEWrapper, in-kernel
                     BF16->FP4 activation quantisation
  bf16               fused_experts (triton) over dequantised weights
  marlin-w4a8fp8     marlin with fp8 activations - refused on NVFP4 weights,
                     kept so the refusal is reproducible

Usage is in README.md next to this file. Everything it needs is inside the
image; it touches nothing outside its own container.
"""
import argparse, json, math, os, statistics, sys, time, types

import torch

FLOAT4_E2M1_MAX = 6.0
FLOAT8_E4M3_MAX = 448.0
QMAX = FLOAT4_E2M1_MAX * FLOAT8_E4M3_MAX  # 2688
WSIGMA = 0.25  # synthetic weight sigma; keeps fp8 block scales in normal range
A2_DQ = 0.0    # post-SiLU dequant global scale, measured once by the gate

LAYOUTS = {
    # name: (E_local, E_global, K hidden, N intermediate per rank, topk)
    "A": dict(E_local=96, E_global=288, K=4096, N=2048, topk=8),
    "B": dict(E_local=288, E_global=288, K=4096, N=768, topk=8),
}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- routing ----
def make_topk(M, topk, n_experts, routing, gen):
    """Return topk_ids [M, topk] int32 with `topk` distinct experts per row."""
    if routing == "uniform":
        w = torch.ones(n_experts, device="cuda", dtype=torch.float32)
    elif routing == "zipf":
        rank = torch.arange(1, n_experts + 1, device="cuda", dtype=torch.float32)
        w = 1.0 / rank  # Zipf s=1 over expert ids
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
    """Build one NVFP4 expert bank in *checkpoint* layout, expert by expert
    (never materialises the bf16 bank).

    returns q [E, rows, K//2] uint8, bs [E, rows, K//16] fp8e4m3 (linear),
            gs2 [E] float32  (dequant global scale = amax/(6*448))
    """
    from vllm import _custom_ops as ops

    g = torch.Generator(device="cuda")
    g.manual_seed(seed)
    q = torch.empty((E, rows, K // 2), dtype=torch.uint8, device="cuda")
    bs = torch.empty((E, rows, K // 16), dtype=torch.float8_e4m3fn, device="cuda")
    gs2 = torch.empty((E,), dtype=torch.float32, device="cuda")
    for e in range(E):
        w = torch.randn((rows, K), device="cuda", dtype=dtype, generator=g) * WSIGMA
        amax = w.abs().amax().to(torch.float32)
        gs_q = (QMAX / amax).reshape(1)  # quantisation global scale
        qe, bse = ops.scaled_fp4_quant(w, gs_q, is_sf_swizzled_layout=False)
        q[e] = qe.view(rows, K // 2)
        bs[e] = bse.view(rows, K // 16)
        gs2[e] = 1.0 / gs_q
        del w, qe, bse
    torch.cuda.empty_cache()
    return q, bs, gs2


def dequant_bank(q, bs, gs2, dtype=torch.bfloat16):
    """[E, rows, K/2] uint8 -> [E, rows, K] bf16, expert by expert."""
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


# ------------------------------------------------------------------- arms ----
class Arm:
    name = "?"
    kind = "?"          # w4a16 / w4a4 / bf16 / w4a8
    supports_map = False

    def __init__(self, L, sets):
        self.L = L
        self.sets = sets  # list of dicts with the raw checkpoint-layout bank

    def prep(self):
        raise NotImplementedError

    def call(self, si, hidden, topk_w, topk_ids, expert_map, global_num_experts):
        raise NotImplementedError

    # bytes of expert weight actually touched, for a given set of used experts
    def bytes_per_expert(self):
        raise NotImplementedError


class BF16Arm(Arm):
    name, kind = "bf16", "bf16"
    supports_map = True

    def prep(self):
        self.w13 = [dequant_bank(s["w13q"], s["w13bs"], s["w13gs2"]) for s in self.sets]
        self.w2 = [dequant_bank(s["w2q"], s["w2bs"], s["w2gs2"]) for s in self.sets]

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        from vllm.model_executor.layers.fused_moe.fused_moe import fused_experts

        return fused_experts(
            hidden, self.w13[si], self.w2[si], topk_w, topk_ids,
            global_num_experts=gne, expert_map=expert_map,
        )

    def bytes_per_expert(self):
        L = self.L
        return (2 * L["N"] * L["K"] + L["K"] * L["N"]) * 2


class MarlinArm(Arm):
    name, kind = "marlin-w4a16", "w4a16"
    supports_map = True
    input_dtype_env = None

    def prep(self):
        from vllm.model_executor.layers.quantization.utils.marlin_utils_fp4 import (
            prepare_nvfp4_moe_layer_for_marlin,
        )
        from vllm.scalar_type import scalar_types

        if self.input_dtype_env is not None:
            os.environ["VLLM_MARLIN_INPUT_DTYPE"] = self.input_dtype_env
            import vllm.envs as envs
            # envs uses module __getattr__ over os.environ -> nothing to reset
        self.quant_type_id = scalar_types.float4_e2m1f.id
        self.packs = []
        L = self.L
        for s in self.sets:
            stub = types.SimpleNamespace(
                num_experts=L["E_local"],
                hidden_size=L["K"],
                intermediate_size_per_partition=L["N"],
                params_dtype=torch.bfloat16,
            )
            w13, w13s, w13s2, w2, w2s, w2s2 = prepare_nvfp4_moe_layer_for_marlin(
                layer=stub,
                w13=s["w13q"], w13_scale=s["w13bs"].view(torch.float8_e4m3fn),
                w13_scale_2=s["w13gs2"],
                w2=s["w2q"], w2_scale=s["w2bs"].view(torch.float8_e4m3fn),
                w2_scale_2=s["w2gs2"],
                is_act_and_mul=True,
            )
            self.packs.append(dict(w13=w13, w13s=w13s, w13s2=w13s2,
                                   w2=w2, w2s=w2s, w2s2=w2s2,
                                   workspace=stub.workspace))
        from vllm.model_executor.layers.quantization.utils.marlin_utils import (
            get_marlin_input_dtype,
        )
        self.input_dtype = get_marlin_input_dtype(prefix="")
        # free the raw bank copies marlin no longer needs
        torch.cuda.empty_cache()

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        from vllm.model_executor.layers.fused_moe.experts.marlin_moe import (
            fused_marlin_moe,
        )

        p = self.packs[si]
        return fused_marlin_moe(
            hidden_states=hidden, w1=p["w13"], w2=p["w2"], bias1=None, bias2=None,
            w1_scale=p["w13s"], w2_scale=p["w2s"],
            topk_weights=topk_w, topk_ids=topk_ids,
            quant_type_id=self.quant_type_id,
            global_num_experts=gne, expert_map=expert_map,
            global_scale1=p["w13s2"], global_scale2=p["w2s2"],
            workspace=p["workspace"], input_dtype=self.input_dtype,
        )

    def bytes_per_expert(self):
        L = self.L
        el = 2 * L["N"] * L["K"] + L["K"] * L["N"]
        return el // 2 + el // 16   # 4-bit weights + fp8 block scales


class MarlinA8Arm(MarlinArm):
    name, kind = "marlin-w4a8fp8", "w4a8"
    input_dtype_env = "fp8"


class CutlassFp4Arm(Arm):
    name, kind = "vllm-cutlass-w4a4", "w4a4"
    supports_map = False

    def prep(self):
        from vllm.model_executor.layers.quantization.utils.nvfp4_utils import (
            swizzle_blockscale,
        )

        L = self.L
        self.packs = []
        for s in self.sets:
            w13s = swizzle_blockscale(s["w13bs"].view(torch.float8_e4m3fn))
            w2s = swizzle_blockscale(s["w2bs"].view(torch.float8_e4m3fn))
            self.packs.append(dict(w13=s["w13q"], w13s=w13s, w13gs2=s["w13gs2"],
                                   w2=s["w2q"], w2s=w2s, w2gs2=s["w2gs2"]))
        torch.cuda.empty_cache()
        self.act_gs = None  # set by set_act_scale()

    def set_act_scale(self, a13_dequant, a2_dequant):
        """a*_dequant = amax/(6*448) per expert (float32 [E])."""
        E = self.L["E_local"]
        self.a13_dq = a13_dequant
        self.a2_dq = a2_dequant
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
        if self.ws13 is None or self.ws13.shape != need1:
            self.ws13 = torch.empty(need1, dtype=torch.bfloat16, device="cuda")
            self.ws2 = torch.empty(need2, dtype=torch.bfloat16, device="cuda")
            self.out = torch.empty((m, L["K"]), dtype=torch.bfloat16, device="cuda")
        run_cutlass_moe_fp4(
            output=self.out, a=hidden,
            a1_gscale=self.a1_gscale, w1_fp4=p["w13"], w1_blockscale=p["w13s"],
            w1_alphas=p["alph13"], a2_gscale=self.a2_gscale,
            w2_fp4=p["w2"], w2_blockscale=p["w2s"], w2_alphas=p["alph2"],
            topk_weights=topk_w, topk_ids=topk_ids,
            activation=MoEActivation.SILU,
            workspace13=self.ws13, workspace2=self.ws2,
            m=m, n=L["N"], k=L["K"], e=L["E_local"], device=hidden.device,
        )
        return self.out

    def bytes_per_expert(self):
        L = self.L
        el = 2 * L["N"] * L["K"] + L["K"] * L["N"]
        return el // 2 + el // 16



class FlashInferB12xArm(Arm):
    """FlashInfer b12x_fused_moe (B12xMoEWrapper) - SM12x FP4 tensor-core MoE.
    In-kernel BF16->FP4 activation quantisation (W4A4), no expert map."""
    name, kind = "fi-b12x-w4a4", "w4a4"
    supports_map = False

    def prep(self):
        from vllm.model_executor.layers.quantization.utils.nvfp4_utils import (
            swizzle_blockscale,
        )
        from vllm.model_executor.layers.quantization.utils.flashinfer_fp4_moe import (
            reorder_w1w3_to_w3w1,
        )
        from vllm.utils.flashinfer import (
            flashinfer_convert_sf_to_mma_layout as conv,
        )
        L = self.L
        self.packs = []
        for s in self.sets:
            w13 = s["w13q"]
            w13bs = s["w13bs"].view(torch.float8_e4m3fn)
            w13, w13bs = reorder_w1w3_to_w3w1(w13, w13bs)
            # bake the per-expert weight global scale into the block scales
            w13bs = (w13bs.float() * s["w13gs2"].view(-1, 1, 1)).to(torch.float8_e4m3fn)
            w2bs = (s["w2bs"].view(torch.float8_e4m3fn).float()
                    * s["w2gs2"].view(-1, 1, 1)).to(torch.float8_e4m3fn)
            w13bs = swizzle_blockscale(w13bs)
            w2bs = swizzle_blockscale(w2bs)
            e1, m1, ksf1 = w13bs.shape
            sf1 = conv(w13bs.reshape(e1 * m1, ksf1), m=m1, k=ksf1 * 16, num_groups=e1)
            e2, m2, ksf2 = w2bs.shape
            sf2 = conv(w2bs.reshape(e2 * m2, ksf2), m=m2, k=ksf2 * 16, num_groups=e2)
            ones = torch.ones(L["E_local"], device="cuda", dtype=torch.float32)
            self.packs.append(dict(w13=w13, sf1=sf1, w2=s["w2q"], sf2=sf2,
                                   a1=ones, a2=ones.clone()))
            del w13bs, w2bs
        torch.cuda.empty_cache()
        self.wrapper = None
        self.max_tokens = 0
        self.cur_topk = -1

    def _ensure(self, M, topk):
        from flashinfer.fused_moe import B12xMoEWrapper
        L = self.L
        if self.wrapper is None or M > self.max_tokens or topk != self.cur_topk:
            self.max_tokens = max(M, self.max_tokens, 2048)
            self.cur_topk = topk
            self.wrapper = B12xMoEWrapper(
                num_experts=L["E_local"], top_k=topk, hidden_size=L["K"],
                intermediate_size=L["N"], use_cuda_graph=True,
                max_num_tokens=self.max_tokens, num_local_experts=L["E_local"],
                activation="silu")

    def call(self, si, hidden, topk_w, topk_ids, expert_map, gne):
        p = self.packs[si]
        self._ensure(hidden.size(0), topk_ids.size(1))
        return self.wrapper.run(
            x=hidden, w1_weight=p["w13"], w1_weight_sf=p["sf1"], w1_alpha=p["a1"],
            fc2_input_scale=p["a2"], w2_weight=p["w2"], w2_weight_sf=p["sf2"],
            w2_alpha=p["a2"], token_selected_experts=topk_ids.to(torch.int32),
            token_final_scales=topk_w)

    def bytes_per_expert(self):
        L = self.L
        el = 2 * L["N"] * L["K"] + L["K"] * L["N"]
        return el // 2 + el // 16


def torch_moe_ref(hidden, w13, w2, topk_w, topk_ids, gq1=None, gq2=None):
    """Plain torch MoE. If gq1/gq2 given, activations are NVFP4
    quantise-dequantised at both GEMM inputs (= emulated W4A4)."""
    import torch.nn.functional as F
    from vllm.model_executor.layers.quantization.utils.nvfp4_emulation_utils import (
        ref_nvfp4_quant_dequant,
    )
    M, K = hidden.shape
    out = torch.zeros(M, K, dtype=torch.float32, device="cuda")
    for e in range(w13.shape[0]):
        sel = (topk_ids == e)
        if not bool(sel.any()):
            continue
        rows, slots = torch.nonzero(sel, as_tuple=True)
        a = hidden[rows].contiguous()
        if gq1 is not None:
            a = ref_nvfp4_quant_dequant(a, gq1[e].reshape(1), 16)
        y = a.float() @ w13[e].float().t()
        g, u = y.chunk(2, dim=-1)
        inter = F.silu(g) * u
        if gq2 is not None:
            inter = ref_nvfp4_quant_dequant(
                inter.to(torch.bfloat16).contiguous(), gq2[e].reshape(1), 16).float()
        z = inter @ w2[e].float().t()
        out.index_add_(0, rows, z * topk_w[rows, slots].unsqueeze(1))
    return out.to(torch.bfloat16)


ARMS = {c.name: c for c in [BF16Arm, MarlinArm, MarlinA8Arm, CutlassFp4Arm, FlashInferB12xArm]}


# ------------------------------------------------------------------ ruler ----
def ruler():
    torch.cuda.synchronize()
    bufs = [torch.randn(512, 32768, dtype=torch.bfloat16, device="cuda")
            for _ in range(4)]
    nbytes = bufs[0].numel() * 2
    for i in range(20):
        bufs[i % 4].sum()
    torch.cuda.synchronize()
    best = []
    for r in range(3):
        st = torch.cuda.Event(True); en = torch.cuda.Event(True)
        st.record()
        for i in range(200):
            bufs[i % 4].sum()
        en.record(); torch.cuda.synchronize()
        ms = st.elapsed_time(en) / 200
        best.append(nbytes / (ms * 1e-3) / 1e9)
    del bufs
    torch.cuda.empty_cache()
    return dict(shape="512x32768 bf16 sum", bytes=nbytes,
                gbps=[round(x, 1) for x in best], gbps_med=round(statistics.median(best), 1))


# ------------------------------------------------------------------ timing ---
def time_call(fn, warmup=20, iters=100, rounds=3):
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


# ------------------------------------------------------------------- main ----
def build_sets(L, n_sets, need_raw=True):
    sets = []
    for i in range(n_sets):
        w13q, w13bs, w13gs2 = quant_expert_bank(L["E_local"], 2 * L["N"], L["K"], 1000 + i)
        w2q, w2bs, w2gs2 = quant_expert_bank(L["E_local"], L["K"], L["N"], 2000 + i)
        sets.append(dict(w13q=w13q, w13bs=w13bs, w13gs2=w13gs2,
                         w2q=w2q, w2bs=w2bs, w2gs2=w2gs2))
    return sets


def mem():
    return dict(alloc_gib=round(torch.cuda.memory_allocated() / 2**30, 2),
                reserved_gib=round(torch.cuda.memory_reserved() / 2**30, 2))


def make_problem(L, form, M, routing, seed):
    """Returns (hidden, topk_w, topk_ids, expert_map, global_num_experts,
                pairs, used_experts)."""
    gen = torch.Generator(device="cuda"); gen.manual_seed(seed)
    K = L["K"]
    if form == "dense":
        ids = make_topk(M, L["topk"], L["E_local"], routing, gen)
        w = make_weights_vec(M, L["topk"], gen)
        hid = (torch.randn(M, K, device="cuda", dtype=torch.bfloat16, generator=gen)
               * 0.5).contiguous()
        return hid, w, ids, None, L["E_local"], M * L["topk"], torch.unique(ids)
    if form == "nativeep":
        ids = make_topk(M, L["topk"], L["E_global"], routing, gen)
        w = make_weights_vec(M, L["topk"], gen)
        emap = torch.full((L["E_global"],), -1, dtype=torch.int32, device="cuda")
        emap[: L["E_local"]] = torch.arange(L["E_local"], dtype=torch.int32,
                                            device="cuda")
        hid = (torch.randn(M, K, device="cuda", dtype=torch.bfloat16, generator=gen)
               * 0.5).contiguous()
        loc = ids[ids < L["E_local"]]
        return hid, w, ids, emap, L["E_global"], int(loc.numel()), torch.unique(loc)
    if form == "epcompact":
        ids = make_topk(M, L["topk"], L["E_global"], routing, gen)
        w = make_weights_vec(M, L["topk"], gen)
        mask = ids < L["E_local"]
        rows = torch.nonzero(mask, as_tuple=False)      # [R, 2] (token, slot)
        R = rows.size(0)
        lid = ids[mask].reshape(R, 1).contiguous()
        lw = w[mask].reshape(R, 1).contiguous()
        hid_full = (torch.randn(M, K, device="cuda", dtype=torch.bfloat16,
                                generator=gen) * 0.5)
        hid = hid_full[rows[:, 0]].contiguous()
        del hid_full
        return hid, lw, lid, None, L["E_local"], R, torch.unique(lid)
    raise ValueError(form)


def cosine(a, b):
    a = a.float().flatten(); b = b.float().flatten()
    return float((a @ b) / (a.norm() * b.norm() + 1e-30))


def relerr(a, b):
    a = a.float(); b = b.float()
    d = (a - b).abs()
    den = b.abs().clamp_min(1e-3)
    return float((d / den).max()), float((d / den).mean())


def cmd_probe(args):
    from vllm.platforms import current_platform
    out = {}
    out["torch"] = torch.__version__
    out["device"] = torch.cuda.get_device_name(0)
    out["capability"] = list(torch.cuda.get_device_capability(0))
    import vllm
    out["vllm"] = vllm.__version__
    try:
        from vllm.model_executor.layers.quantization.utils.nvfp4_utils import (
            cutlass_fp4_supported,
        )
        out["cutlass_fp4_supported"] = bool(cutlass_fp4_supported())
    except Exception as e:
        out["cutlass_fp4_supported"] = f"ERR {e}"
    for op in ["cutlass_fp4_moe_mm", "scaled_fp4_experts_quant",
               "silu_and_mul_scaled_fp4_experts_quant", "scaled_fp4_quant",
               "gptq_marlin_repack", "moe_wna16_marlin_gemm"]:
        try:
            from vllm import _custom_ops as ops
            out["op_" + op] = hasattr(ops, op)
        except Exception as e:
            out["op_" + op] = f"ERR {e}"
    for mod, cls in [
        ("vllm.model_executor.layers.fused_moe.experts.flashinfer_cutlass_moe",
         "FlashInferExperts"),
        ("vllm.model_executor.layers.fused_moe.experts.trtllm_nvfp4_moe",
         "TrtLlmNvFp4ExpertsMonolithic"),
        ("vllm.model_executor.layers.fused_moe.b12x", "B12xExperts"),
        ("vllm.model_executor.layers.fused_moe.experts.flashinfer_b12x_moe",
         "FlashInferB12xExperts"),
        ("vllm.model_executor.layers.fused_moe.experts.cutlass_moe",
         "CutlassExpertsFp4"),
    ]:
        try:
            m = __import__(mod, fromlist=[cls])
            c = getattr(m, cls)
            out[cls + "._supports_current_device"] = bool(c._supports_current_device())
        except Exception as e:
            out[cls + "._supports_current_device"] = f"ERR {type(e).__name__}: {e}"
    try:
        import b12x
        out["b12x"] = getattr(b12x, "__version__", "?")
    except Exception as e:
        out["b12x"] = f"ERR {e}"
    try:
        import flashinfer
        out["flashinfer"] = flashinfer.__version__
    except Exception as e:
        out["flashinfer"] = f"ERR {e}"
    out["ruler"] = ruler()
    print(json.dumps(out, indent=1))


def _act_scales(hidden, E, topk_ids=None):
    """dequant global scale per expert = amax/(6*448); one amax for all."""
    amax = hidden.abs().amax().to(torch.float32)
    return (amax / QMAX).repeat(E).contiguous()


def _a2_scales(hidden, sets, L, E):
    """Estimate the amax of the post-SiLU intermediate with one expert's w13
    (all experts are statistically identical here), so the second FP4 quant
    is not fed a wrong global scale."""
    s = sets[0]
    w13_0 = dequant_bank(s["w13q"][:1], s["w13bs"][:1], s["w13gs2"][:1])[0]
    a = hidden[: min(hidden.size(0), 256)].float()
    y = (a @ w13_0.float().t())
    g, u = y.chunk(2, dim=-1)
    inter = torch.nn.functional.silu(g) * u
    amax = inter.abs().amax().to(torch.float32)
    del w13_0, y, g, u, inter
    torch.cuda.empty_cache()
    return (amax / QMAX).repeat(E).contiguous()


def cmd_gate(args):
    """Correctness gate: small expert bank, full production shapes, identical
    inputs, one dequantised reference. Two yardsticks:
      * bf16-of-dequantised-weights  (what the arm *should* compute)
      * emulated W4A4               (what an FP4-activation path must compute)
    """
    L = dict(LAYOUTS[args.layout])
    L["E_local"] = args.gate_experts
    L["E_global"] = args.gate_experts
    sets = build_sets(L, 1)
    M = args.M[0]
    hid, w, ids, emap, gne, pairs, used = make_problem(L, "dense", M, "uniform", 7)

    res = {"layout": args.layout, "M": M, "E_gate": L["E_local"],
           "K": L["K"], "N": L["N"], "topk": L["topk"], "arms": {}}

    # references (built once, then freed)
    w13bf = dequant_bank(sets[0]["w13q"], sets[0]["w13bs"], sets[0]["w13gs2"])
    w2bf = dequant_bank(sets[0]["w2q"], sets[0]["w2bs"], sets[0]["w2gs2"])
    a13 = _act_scales(hid, L["E_local"])
    a2 = _a2_scales(hid, sets, L, L["E_local"])
    res["a13_dq"] = float(a13[0]); res["a2_dq"] = float(a2[0])
    ref_bf16 = torch_moe_ref(hid, w13bf, w2bf, w, ids)
    ref_w4a4 = torch_moe_ref(hid, w13bf, w2bf, w, ids,
                             gq1=(1.0 / a13), gq2=(1.0 / a2))
    del w13bf, w2bf
    torch.cuda.empty_cache()
    res["ref_w4a4_vs_bf16"] = {
        "cosine": round(cosine(ref_w4a4, ref_bf16), 6),
        "note": "intrinsic W4A4 penalty on synthetic Gaussian data"}

    outs = {}
    for nm in args.arms:
        try:
            arm = ARMS[nm](L, sets)
            arm.prep()
            if isinstance(arm, CutlassFp4Arm):
                arm.set_act_scale(a13, a2)
                best = None
                for conv in ("recip", "direct"):
                    arm.a1_gscale = ((1.0 / a13) if conv == "recip" else a13).contiguous()
                    arm.a2_gscale = ((1.0 / a2) if conv == "recip" else a2).contiguous()
                    try:
                        o = arm.call(0, hid, w, ids, None, gne).clone()
                    except Exception:
                        continue
                    c = cosine(o, ref_w4a4)
                    if best is None or c > best[1]:
                        best = (conv, c, o)
                res["cutlass_gscale_convention"] = best[0]
                out = best[2]
            else:
                out = arm.call(0, hid, w, ids, None, gne).clone()
            outs[nm] = out
            del arm
            torch.cuda.empty_cache()
        except Exception as e:
            import traceback
            res["arms"][nm] = {"status": "FAILED",
                               "error": f"{type(e).__name__}: {e}",
                               "tb": traceback.format_exc()[-1800:]}
            torch.cuda.empty_cache()

    def rl2(a, b):
        a = a.float(); b = b.float()
        return float((a - b).norm() / b.norm())
    res["rel_l2_vs_bf16"] = {"emul_w4a4_reference": round(rl2(ref_w4a4, ref_bf16), 5)}
    res["pairwise_cosine"] = {}
    names = list(outs.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            res["pairwise_cosine"][names[i] + " ~ " + names[j]] = round(
                cosine(outs[names[i]], outs[names[j]]), 6)
    for nm, o in outs.items():
        res["rel_l2_vs_bf16"][nm] = round(rl2(o, ref_bf16), 5)
        mx, mn = relerr(o, ref_bf16)
        mx4, mn4 = relerr(o, ref_w4a4)
        cb = cosine(o, ref_bf16); c4 = cosine(o, ref_w4a4)
        kind = ARMS[nm].kind
        gate_ref = "w4a4" if kind == "w4a4" else "bf16"
        cgate = c4 if gate_ref == "w4a4" else cb
        res["arms"][nm] = {
            "status": "ok", "kind": kind,
            "cosine_vs_bf16": round(cb, 6), "cosine_vs_emul_w4a4": round(c4, 6),
            "relerr_max_vs_bf16": round(mx, 3), "relerr_mean_vs_bf16": round(mn, 5),
            "relerr_max_vs_w4a4": round(mx4, 3), "relerr_mean_vs_w4a4": round(mn4, 5),
            "out_absmax": round(float(o.abs().max()), 4),
            "gate_reference": gate_ref,
            "VERDICT": "COMPARABLE" if cgate >= 0.99 else "NOT COMPARABLE"}
    print(json.dumps(res, indent=1))


def cmd_time(args):
    L = dict(LAYOUTS[args.layout])
    n_sets = args.sets
    t0 = time.time()
    sets = build_sets(L, n_sets)
    bank_bytes = sum(s["w13q"].numel() + s["w13bs"].numel() + s["w2q"].numel()
                     + s["w2bs"].numel() for s in sets)
    out = {"layout": args.layout, "arm": args.arm, "sets": n_sets,
           "shapes": L, "bank_raw_gib": round(bank_bytes / 2**30, 3),
           "build_s": round(time.time() - t0, 1), "runs": [], "ruler": None}
    arm = ARMS[args.arm](L, sets)
    arm.prep()
    # drop the raw checkpoint-layout bank; each arm keeps only what it needs
    arm.sets = None
    for s_ in sets:
        s_.clear()
    torch.cuda.empty_cache()
    out["mem_after_prep"] = mem()
    if args.arm == "vllm-cutlass-w4a4":
        pass  # act scales set per problem below
    out["ruler"] = ruler()

    forms = args.forms
    for form in forms:
        if form in ("nativeep", "epcompact") and args.layout != "A":
            continue
        if form == "nativeep" and not ARMS[args.arm].supports_map:
            continue
        for M in args.M:
            for routing in args.routing:
                try:
                    hid, w, ids, emap, gne, pairs, used = make_problem(
                        L, form, M, routing, 11 + M)
                    if args.arm == "vllm-cutlass-w4a4":
                        a13 = _act_scales(hid, L["E_local"])
                        a2 = (torch.full_like(a13, A2_DQ) if A2_DQ else a13.clone())
                        arm.set_act_scale(a13, a2)
                        if args.gscale == "recip":
                            arm.a1_gscale = (1.0 / a13).contiguous()
                            arm.a2_gscale = (1.0 / a2).contiguous()
                        else:
                            arm.a1_gscale = a13.contiguous()
                            arm.a2_gscale = a2.contiguous()
                        arm.ws13 = None
                    fn = lambda i: arm.call(i % n_sets, hid, w, ids, emap, gne)
                    us = time_call(fn, args.warmup, args.iters, args.rounds)
                    nexp = int(used.numel())
                    tb = nexp * arm.bytes_per_expert()
                    flops = pairs * 6.0 * L["N"] * L["K"]
                    med = statistics.median(us)
                    out["runs"].append(dict(
                        form=form, M=M, routing=routing, rows_or_pairs=pairs,
                        experts_touched=nexp, us_rounds=[round(x, 2) for x in us],
                        us=round(med, 2),
                        spread_pct=round((max(us) - min(us)) / med * 100, 2),
                        touched_gib=round(tb / 2**30, 3),
                        gbps=round(tb / (med * 1e-6) / 1e9, 1),
                        tflops=round(flops / (med * 1e-6) / 1e12, 2)))
                    log(f"  {form:10s} M={M:5d} {routing:8s} -> {med:9.2f} us  "
                        f"{out['runs'][-1]['gbps']:7.1f} GB/s "
                        f"{out['runs'][-1]['tflops']:6.2f} TF")
                    del hid, w, ids
                    torch.cuda.empty_cache()
                except Exception as e:
                    import traceback
                    out["runs"].append(dict(form=form, M=M, routing=routing,
                                            status="FAILED",
                                            error=f"{type(e).__name__}: {e}",
                                            tb=traceback.format_exc()[-1200:]))
                    log(f"  {form:10s} M={M:5d} {routing:8s} -> FAILED {e}")
                    torch.cuda.empty_cache()
    out["mem_peak"] = dict(peak_gib=round(torch.cuda.max_memory_allocated() / 2**30, 2))
    with open(args.out, "w") as f:
        json.dump(out, f, indent=1)
    log("WROTE " + args.out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["probe", "gate", "time"])
    p.add_argument("--layout", default="A", choices=["A", "B"])
    p.add_argument("--arm", default="marlin-w4a16")
    p.add_argument("--arms", nargs="+",
                   default=["bf16", "marlin-w4a16", "vllm-cutlass-w4a4"])
    p.add_argument("--M", nargs="+", type=int, default=[8, 64, 1792])
    p.add_argument("--routing", nargs="+", default=["uniform", "zipf"])
    p.add_argument("--forms", nargs="+", default=["dense", "epcompact", "nativeep"])
    p.add_argument("--sets", type=int, default=2)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--iters", type=int, default=100)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--gate-experts", type=int, default=24)
    p.add_argument("--gscale", default="recip", choices=["recip", "direct"])
    p.add_argument("--a2dq", type=float, default=0.0)
    p.add_argument("--out", default="/bench/out.json")
    args = p.parse_args()
    globals()["A2_DQ"] = args.a2dq
    torch.cuda.init()
    from vllm.config import VllmConfig, set_current_vllm_config
    try:
        ctx = set_current_vllm_config(VllmConfig())
    except Exception as e:
        log("WARN: no VllmConfig context (%s)" % e)
        import contextlib
        ctx = contextlib.nullcontext()
    with ctx:
        if args.mode == "probe":
            cmd_probe(args)
        elif args.mode == "gate":
            cmd_gate(args)
        else:
            cmd_time(args)


if __name__ == "__main__":
    main()
