#!/usr/bin/env python3
"""Community-style benchmark, kept close to the protocol other DGX Spark owners
publish (sparkDash and friends): temperature 0, 400 tokens, three fixed prompts
-- "count 1 to 200" (structured), "clamp_00..clamp_49" (code) and a short prose
explanation.

These are SYNTHETIC prompts. They show the speculative-decoding CEILING, not
what you will see in real use; label any number from this script accordingly.

NOTE: thinking cannot be turned off on this model, so the run uses
reasoning_effort=low and reports the reasoning/content token split, which is
the fairest comparison we can offer against a no-thinking number.

Usage: python3 community-protocol.py [API_BASE]    default http://192.0.2.10:8000
Written by us for this recipe; use freely (Apache-2.0)."""
import json, os, sys, time, urllib.request, statistics
API = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("API", "http://192.0.2.10:8000")
P = {"structured(count 1->200)": "Count from 1 to 200, one number per line, nothing else.",
     "code(clamp_00..49)": "Write Python: define 50 functions clamp_00 ... clamp_49, each `def clamp_XX(x): return max(0, min(XX, x))` with XX the index, one per line, no commentary.",
     "prose(hash-map)": "Explain what a hash map is and how it works, in about 300 words of plain English prose."}
def metrics():
    raw = urllib.request.urlopen(API + "/metrics", timeout=10).read().decode(); out = {}
    for line in raw.splitlines():
        for k in ("spec_decode_num_draft_tokens_total", "spec_decode_num_accepted_tokens_total", "spec_decode_num_drafts_total"):
            if line.startswith("vllm:" + k): out[k] = float(line.rsplit(" ", 1)[1])
    return out
def run(prompt, effort="low"):
    body = {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": prompt}], "max_tokens": 400, "temperature": 0.0,
            "stream": True, "stream_options": {"include_usage": True}, "chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": effort}}
    req = urllib.request.Request(API + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time(); tf = None; usage = None; think = 0; content = 0
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data:") or line == "data: [DONE]": continue
            d = json.loads(line[5:]); usage = d.get("usage") or usage
            ch = d.get("choices") or []
            if ch:
                dl = ch[0].get("delta") or {}
                if dl.get("reasoning_content"): think += 1
                if dl.get("content"): content += 1
                if tf is None and (dl.get("content") or dl.get("reasoning_content")): tf = time.time()
    t1 = time.time(); ct = (usage or {}).get("completion_tokens", 0)
    return (ct - 1) / max(t1 - (tf or t1), 1e-6), (tf or t1) - t0, ct, think, content
print(f"=== community protocol (temp 0, 400 tokens, thinking low) {time.strftime('%H:%M')}")
for name, p in P.items():
    run(p)  # warm-up (fills the prefix cache)
    vals = []; m0 = metrics()
    for _ in range(3): vals.append(run(p))
    m1 = metrics(); dd = m1["spec_decode_num_draft_tokens_total"] - m0["spec_decode_num_draft_tokens_total"]; da = m1["spec_decode_num_accepted_tokens_total"] - m0["spec_decode_num_accepted_tokens_total"]; ds = m1["spec_decode_num_drafts_total"] - m0["spec_decode_num_drafts_total"]
    print(f"  {name:26s} decode median={statistics.median(v[0] for v in vals):5.1f} tok/s | TTFT={statistics.median(v[1] for v in vals):.2f}s | acceptance={100*da/dd:.1f}% | tokens per step={(da+ds)/ds if ds else 0:.2f} | tokens={int(statistics.median(v[2] for v in vals))} (reasoning ~{int(statistics.median(v[3] for v in vals))}, content ~{int(statistics.median(v[4] for v in vals))})", flush=True)
print(f"=== DONE {time.strftime('%H:%M')}")
