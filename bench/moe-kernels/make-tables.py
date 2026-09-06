#!/usr/bin/env python3
"""Aggregate the MoE kernel bench JSONs into markdown tables.

    python3 make-tables.py [dir] > TABLES.md

`dir` defaults to ./out next to this script. To regenerate the published
tables from the raw files in this repository:

    python3 bench/moe-kernels/make-tables.py results/kernels/gb10-moebench
"""
import glob, json, os, sys

OUT = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
ARMS = ["marlin-w4a16", "vllm-cutlass-w4a4", "fi-b12x-w4a4", "bf16"]
LBL = {"marlin-w4a16": "marlin W4A16", "vllm-cutlass-w4a4": "cutlass W4A4",
       "fi-b12x-w4a4": "b12x W4A4", "bf16": "bf16"}

data = {}   # (layout, arm) -> {(form, M, routing): run}
meta = {}
for f in sorted(glob.glob(os.path.join(OUT, "[AB]-*.json"))):
    d = json.load(open(f))
    key = (d["layout"], d["arm"])
    data.setdefault(key, {})
    meta[key] = d
    for r in d["runs"]:
        if r.get("status") == "FAILED":
            continue
        data[key][(r["form"], r["M"], r["routing"])] = r


def table(layout, form, title, arms=ARMS):
    rows = []
    hdr = ["M", "routing", "pairs", "experts"]
    for a in arms:
        hdr += [LBL[a] + " us", "x marlin"]
    lines = ["| " + " | ".join(hdr) + " |",
             "|" + "|".join(["---"] * len(hdr)) + "|"]
    for M in (8, 64, 1792):
        for rt in ("uniform", "zipf"):
            base = data.get((layout, "marlin-w4a16"), {}).get((form, M, rt))
            if base is None:
                continue
            cells = [str(M), rt, str(base["rows_or_pairs"]),
                     str(base["experts_touched"])]
            for a in arms:
                r = data.get((layout, a), {}).get((form, M, rt))
                if r is None:
                    cells += ["-", "-"]
                else:
                    cells += ["%.0f" % r["us"], "%.2f" % (r["us"] / base["us"])]
            lines.append("| " + " | ".join(cells) + " |")
    return "**%s**\n\n" % title + "\n".join(lines) + "\n"


def bwtable(layout, form, title, arms=ARMS):
    hdr = ["M", "routing"]
    for a in arms:
        hdr += [LBL[a] + " GB/s", LBL[a] + " TFLOPS"]
    lines = ["| " + " | ".join(hdr) + " |",
             "|" + "|".join(["---"] * len(hdr)) + "|"]
    for M in (8, 64, 1792):
        for rt in ("uniform", "zipf"):
            cells = [str(M), rt]
            any_ = False
            for a in arms:
                r = data.get((layout, a), {}).get((form, M, rt))
                if r is None:
                    cells += ["-", "-"]
                else:
                    any_ = True
                    cells += ["%.0f" % r["gbps"], "%.1f" % r["tflops"]]
            if any_:
                lines.append("| " + " | ".join(cells) + " |")
    return "**%s**\n\n" % title + "\n".join(lines) + "\n"


def spreadtable():
    lines = ["| layout | arm | max round-to-round spread | median spread |",
             "|---|---|---|---|"]
    for k, runs in sorted(data.items()):
        sp = [r["spread_pct"] for r in runs.values()]
        if not sp:
            continue
        sp.sort()
        lines.append("| %s | %s | %.1f %% | %.1f %% |"
                     % (k[0], LBL.get(k[1], k[1]), max(sp), sp[len(sp) // 2]))
    return "\n".join(lines) + "\n"


def banks():
    lines = ["| layout | arm | weight sets | raw bank / set | resident after prep | peak |",
             "|---|---|---|---|---|---|"]
    for k, d in sorted(meta.items()):
        # bank_raw_gib is the whole bank; report it per weight set
        lines.append("| %s | %s | %d | %.2f GiB | %.2f GiB | %.2f GiB |" % (
            k[0], LBL.get(k[1], k[1]), d["sets"], d["bank_raw_gib"] / d["sets"],
            d["mem_after_prep"]["alloc_gib"], d["mem_peak"]["peak_gib"]))
    return "\n".join(lines) + "\n"


def epvsnative():
    lines = ["| M | routing | marlin native-EP (288 ids + expert_map) us | marlin ep-compacted us | ratio |",
             "|---|---|---|---|---|"]
    for M in (8, 64, 1792):
        for rt in ("uniform", "zipf"):
            n = data.get(("A", "marlin-w4a16"), {}).get(("nativeep", M, rt))
            c = data.get(("A", "marlin-w4a16"), {}).get(("epcompact", M, rt))
            if n and c:
                lines.append("| %d | %s | %.0f | %.0f | %.2f |"
                             % (M, rt, n["us"], c["us"], n["us"] / c["us"]))
    return "\n".join(lines) + "\n"


def ab():
    """Layout A (EP, ep-compacted) vs Layout B (no-EP, dense) per arm."""
    lines = ["| M | routing | arm | A ep-compacted us | B (2304/3, all-local) us | B / A |",
             "|---|---|---|---|---|---|"]
    for M in (8, 64, 1792):
        for rt in ("uniform", "zipf"):
            for a in ARMS:
                x = data.get(("A", a), {}).get(("epcompact", M, rt))
                y = data.get(("B", a), {}).get(("dense", M, rt))
                if x and y:
                    lines.append("| %d | %s | %s | %.0f | %.0f | %.2f |"
                                 % (M, rt, LBL[a], x["us"], y["us"],
                                    y["us"] / x["us"]))
    return "\n".join(lines) + "\n"


print(table("A", "dense", "T1. Layout A, all-8-local form (96 local experts, K=4096, N=2048, top-8 of 96)"))
print(table("A", "epcompact", "T2. Layout A, EP-effective form (top-8 over 288, non-local dropped; topk=1 over the local pair list)"))
print(epvsnative())
print(table("B", "dense", "T4. Layout B - TP-2304 no-EP (288 local experts, K=4096, N=768, top-8 of 288)"))
print(bwtable("A", "dense", "T5. Layout A all-8-local - achieved bandwidth and TFLOPS"))
print(bwtable("A", "epcompact", "T6. Layout A EP-effective - achieved bandwidth and TFLOPS"))
print(bwtable("B", "dense", "T7. Layout B - achieved bandwidth and TFLOPS"))
print("**T8. Round-to-round spread**\n")
print(spreadtable())
print("**T9. Weight banks (memory hygiene)**\n")
print(banks())
print("**T10. Layout A (EP) vs Layout B (no-EP) at equal token traffic**\n")
print(ab())
