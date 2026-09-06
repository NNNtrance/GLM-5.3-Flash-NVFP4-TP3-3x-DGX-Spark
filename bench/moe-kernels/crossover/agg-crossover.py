#!/usr/bin/env python3
"""Turn the crossover sweep JSONs into the tables of
results/kernels/fp4-crossover-sweep-gb10.md.

Roofline model (documented so it can be attacked):
  ceiling C      = best of the three rulers, GB/s
  weight bytes W = experts_touched x bytes_per_expert (MEASURED resident bytes)
  act bytes    A = (rows_in + rows_out) x K x 2   (bf16 in, bf16 out, each once)
  t_mem          = (W + A) / C
  logical FLOPs  = pairs x 6 x N x K
  t_fp4          = FLOPs / 500e12   (spec dense FP4 tensor peak, sm_121)
  t_bf16         = FLOPs / 97.3e12  (our measured BF16 tensor throughput)
  roofline_fp4   = max(t_mem, t_fp4)     roofline_bf16 = max(t_mem, t_bf16)
No kernel of any design can go below its roofline; "% of roofline" = roofline/measured.
"""
import json, os, sys, statistics

OUT = os.environ.get("XSWEEP_OUT", "/var/tmp/xsweep/out")
FP4_PEAK = 500e12
BF16_PEAK = 97.3e12
OPPOINTS = [8, 64, 128, 256, 1792]


def jload(n):
    p = os.path.join(OUT, n + ".json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def key(r):
    return (r["form"], r["M"], r["routing"])


def index(d):
    if not d:
        return {}
    return {key(r): r for r in d["runs"] if r.get("status") != "FAILED"}


def main():
    ruler = jload("ruler")
    marlin, cutlass = jload("marlin"), jload("cutlass")
    b12m, b12f = jload("b12x-matched"), jload("b12x-fixed2k")
    split, bf16 = jload("split"), jload("bf16")
    MI, CI, BM, BF, SP, F16 = (index(x) for x in
                               (marlin, cutlass, b12m, b12f, split, bf16))

    C = None
    lines = []
    P = lines.append

    # ------------------------------------------------------------- rulers ---
    P("### R1. Memory ceiling, measured three ways\n")
    P("| ruler | method | buffer | GB/s | rounds |")
    P("|---|---|---|---|---|")
    if ruler:
        for nm, v in ruler["rulers"].items():
            if "gbps" not in v:
                P(f"| {nm} | FAILED | - | - | {v.get('error','')[:60]} |")
                continue
            P(f"| {nm} | {v['method']} | {v.get('buf_mib','?')} MiB | "
              f"**{v['gbps']}** | {v.get('rounds_gbps')} |")
        C = ruler.get("ceiling_gbps")
        P(f"| spec | LPDDR5X datasheet peak | - | {ruler['spec_lpddr5x_gbps']} | - |")
        P("")
        P(f"**Ceiling used below: {C} GB/s** (best of the three measured rulers; "
          f"{100*C/ruler['spec_lpddr5x_gbps']:.0f} % of the datasheet peak).\n")
    if C is None:
        C = 240.5
        P(f"_ruler.json missing; falling back to the 6 Sep ruler {C} GB/s_\n")
    Cb = C * 1e9

    # ------------------------------------------- measured weight-byte check --
    P("### R2. Measured resident weight bytes per expert (byte accounting check)\n")
    P("| arm | bytes/expert measured | vs 4-bit + fp8-scale model |")
    P("|---|---|---|")
    model = (2 * 2048 * 4096 + 4096 * 2048) * (0.5 + 1 / 16)
    for nm, d in (("marlin W4A16", marlin), ("cutlass W4A4", cutlass),
                  ("b12x W4A4", b12m), ("bf16", bf16)):
        if not d:
            continue
        b = d["bytes_per_expert_measured"]
        P(f"| {nm} | {b:,} | {b/model:.4f}x |")
    P("")

    # ---------------------------------------------------------- main sweep ---
    forms = ["dense", "epcompact"]
    routings = ["uniform", "zipf"]
    cross = {}
    for form in forms:
        for rt in routings:
            P(f"### T. Layout A - form `{form}`, routing `{rt}`\n")
            P("| M | rows | experts | marlin us | cutlass us | x mar | b12x us | "
              "x mar | bf16 us | marlin GB/s | best-FP4 GB/s | marlin TF | best-FP4 TF |")
            P("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            firstwin = None
            Ms = sorted({k[1] for k in MI if k[0] == form and k[2] == rt})

            def cell(r, fmt="{:.0f}"):
                return fmt.format(r["us"]) if r else "-"

            for M in Ms:
                k = (form, M, rt)
                m = MI.get(k)
                if not m:
                    continue
                c, b, f = CI.get(k), BM.get(k), F16.get(k)
                fp4 = [x for x in (c, b) if x]
                bestfp4 = min(fp4, key=lambda r: r["us"]) if fp4 else None
                r_c = f"{c['us']/m['us']:.2f}" if c else "-"
                r_b = f"{b['us']/m['us']:.2f}" if b else "-"
                if bestfp4 and firstwin is None and bestfp4["us"] < 0.95 * m["us"]:
                    firstwin = M
                P("| " + " | ".join([
                    str(M), str(m["rows"]), str(m["experts_touched"]),
                    f"{m['us']:.0f}", cell(c), r_c, cell(b), r_b, cell(f),
                    str(m["gbps"]),
                    str(bestfp4["gbps"]) if bestfp4 else "-",
                    str(m["tflops"]),
                    str(bestfp4["tflops"]) if bestfp4 else "-",
                ]) + " |")
            cross[(form, rt)] = firstwin
            P("")
            P(f"**Crossover M (best FP4 first beats marlin by > 5 %): "
              f"{firstwin if firstwin else 'NONE in 8..4096'}**\n")

    # ------------------------------------------------- b12x sizing A/B ------
    if BF:
        P("### T. b12x wrapper sizing A/B (`max_num_tokens` matched to M vs "
          "always >= 2048, as the 6 Sep bench ran it)\n")
        P("| form | M | routing | matched us | fixed>=2048 us | fixed/matched |")
        P("|---|---|---|---|---|---|")
        for form in forms:
            for M in sorted({k[1] for k in BM if k[0] == form}):
                for rt in routings:
                    a, b = BM.get((form, M, rt)), BF.get((form, M, rt))
                    if not (a and b):
                        continue
                    P(f"| {form} | {M} | {rt} | {a['us']:.0f} | {b['us']:.0f} | "
                      f"{b['us']/a['us']:.3f} |")
        P("")

    # ------------------------------------------------- GEMM-only bound ------
    if SP:
        P("### T. GEMM-only bound: what a perfectly fused custom FP4 kernel "
          "cannot go below\n")
        P("`gemm-only` = the two grouped FP4 tensor-core GEMMs with "
          "pre-quantised activations, pre-built routing metadata, no row "
          "shuffle and no epilogue combine.\n")
        P("| form | M | routing | marlin us | cutlass full us | GEMM-only us | "
          "act-quant us | meta+combine us | GEMM-only / marlin | GEMM-only GB/s | GEMM-only TF |")
        P("|---|---|---|---|---|---|---|---|---|---|---|")
        for form in forms:
            for M in sorted({k[1] for k in SP if k[0] == form}):
                for rt in routings:
                    s, m = SP.get((form, M, rt)), MI.get((form, M, rt))
                    if not (s and m):
                        continue
                    P(f"| {form} | {M} | {rt} | {m['us']:.0f} | {s['us_full']:.0f} | "
                      f"**{s['us_gemm_only']:.0f}** | {s['us_actquant']:.0f} | "
                      f"{s['us_meta_combine']:.0f} | "
                      f"**{s['us_gemm_only']/m['us']:.2f}x** | {s['gemm_gbps']} | "
                      f"{s['gemm_tflops']} |")
        P("")

    # ------------------------------------------------------- roofline -------
    P("### T. Roofline and % of roofline\n")
    P("| form | M | routing | weight MiB | t_mem us | t_fp4 us | t_bf16 us | "
      "roofline_fp4 us | roofline_bf16 us | marlin us | marlin % of roofline | "
      "best FP4 us | best FP4 % of roofline | honest ceiling (marlin/roofline_fp4) |")
    P("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    RF = {}
    for form in forms:
        for M in sorted({k[1] for k in MI if k[0] == form}):
            for rt in routings:
                k = (form, M, rt)
                m = MI.get(k)
                if not m:
                    continue
                W = m["weight_bytes"]
                A = 2 * m["rows"] * 4096 * 2
                t_mem = (W + A) / Cb * 1e6
                fl = m["gflop"] * 1e9
                t_fp4 = fl / FP4_PEAK * 1e6
                t_bf = fl / BF16_PEAK * 1e6
                rf4, rfb = max(t_mem, t_fp4), max(t_mem, t_bf)
                cands = [x for x in (CI.get(k), BM.get(k)) if x]
                best = min(cands, key=lambda r: r["us"]) if cands else None
                RF[k] = dict(t_mem=t_mem, rf4=rf4, rfb=rfb, marlin=m["us"],
                             best=best["us"] if best else None,
                             gemm=SP.get(k, {}).get("us_gemm_only"))
                P("| " + " | ".join([
                    form, str(M), rt, f"{W/2**20:.0f}", f"{t_mem:.0f}",
                    f"{t_fp4:.0f}", f"{t_bf:.0f}", f"{rf4:.0f}", f"{rfb:.0f}",
                    f"{m['us']:.0f}", f"{100*rfb/m['us']:.0f} %",
                    f"{best['us']:.0f}" if best else "-",
                    f"{100*rf4/best['us']:.0f} %" if best else "-",
                    f"**{m['us']/rf4:.2f}x**",
                ]) + " |")
    P("")

    # --------------------------------------------------- verdict table ------
    P("### V. Pre-registered verdict at every operating point\n")
    P("Criterion A: GEMM-only FP4 beats marlin by > 10 % (`gemm-only/marlin < 0.90`).  ")
    P("Criterion B: roofline_fp4 more than 1.25x faster than measured marlin "
      "(`marlin/roofline_fp4 > 1.25`).  ")
    P("SUPPORTED if A or B holds, otherwise NOT SUPPORTED.\n")
    P("| operating point | form | routing | marlin us | GEMM-only us | A: gemm/marlin | "
      "B: marlin/roofline_fp4 | max gain of a custom path | VERDICT |")
    P("|---|---|---|---|---|---|---|---|---|")
    names = {8: "M=8 (1 stream, DFlash2 k=7)", 64: "M=64 (8 streams)",
             128: "M=128 (16 streams)", 256: "M=256 (32 streams)",
             1792: "M=1792 (prefill chunk)"}
    for M in OPPOINTS:
        for form in forms:
            for rt in ["uniform"]:
                k = (form, M, rt)
                r = RF.get(k)
                if not r:
                    continue
                g = r["gemm"]
                A_ok = (g is not None) and (g / r["marlin"] < 0.90)
                B = r["marlin"] / r["rf4"]
                B_ok = B > 1.25
                gain = (1 - r["rf4"] / r["marlin"]) * 100
                v = "**SUPPORTED**" if (A_ok or B_ok) else "NOT SUPPORTED"
                P(f"| {names[M]} | {form} | {rt} | {r['marlin']:.0f} | "
                  f"{g:.0f} | {g/r['marlin']:.2f} {'YES' if A_ok else 'no'} | "
                  f"{B:.2f} {'YES' if B_ok else 'no'} | {gain:.0f} % | {v} |"
                  if g is not None else
                  f"| {names[M]} | {form} | {rt} | {r['marlin']:.0f} | - | - | "
                  f"{B:.2f} {'YES' if B_ok else 'no'} | {gain:.0f} % | {v} |")
    P("")

    # --------------------------------------------------------- hygiene ------
    P("### H. Hygiene\n")
    P("| stage | build s | mem peak GiB | MemAvailable min GiB | sets |")
    P("|---|---|---|---|---|")
    for nm, d in (("ruler", ruler), ("marlin", marlin), ("cutlass", cutlass),
                  ("b12x matched", b12m), ("b12x fixed>=2048", b12f),
                  ("cutlass split", split), ("bf16", bf16)):
        if not d:
            P(f"| {nm} | - | - | - | NOT RUN |")
            continue
        P(f"| {nm} | {d.get('build_s','-')} | {d.get('mem_peak_gib','-')} | "
          f"{d.get('memavail_min_gib','-')} | {d.get('sets','-')} |")
    P("")

    spreads = []
    for d in (marlin, cutlass, b12m, bf16):
        if d:
            spreads += [r["spread_pct"] for r in d["runs"] if "spread_pct" in r]
    if spreads:
        P(f"Round-to-round spread across all timed points: median "
          f"{statistics.median(spreads):.2f} %, max {max(spreads):.2f} %.\n")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
