# 10 — The production candidate, and the five lessons we tried to carry over

This is the closing state of this recipe, measured on **2026-09-05/06**. Everything in
[03 — Launch and flags](03-launch-and-flags.md) still describes a working production setup; this
document adds the configuration we would ship **today** and says exactly what each change bought,
including the three that bought nothing.

The work was done as a transfer experiment. Our [EXL3 sibling recipe](https://github.com/NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark)
produced five findings that are, on their face, independent of the quantization format — a transport
patch, a collective-channel setting, a chat template, a page-cache/allocator cleanup, and a boot
gate. Each was carried into a **separate tree** on the three nodes, applied one at a time, and
measured. The untouched production tree was the control arm.

Evidence tiers are the ones from the [style guide](../STYLE-GUIDE.md), plus
`[measured-here, raw not published]` for things measured on this cluster whose raw file is not in
[`results/`](../results/) — it exists on our nodes only.

---

## 1. The candidate configuration

Four things change against the production launch documented in [03](03-launch-and-flags.md).
`gpu-memory-utilization` stays at **0.88**; it was not touched in any arm.

### 1.1 The environment lines

```text
NCCL_MAX_NCHANNELS=8
NCCL_MESH_LINKS_PER_PEER=0
NCCL_MESH_MIN_RNR_TIMER=1
NCCL_MESH_PTR_CUDA=1
NCCL_MESH_FLUSH=1
```

`NCCL_MESH_PLUGIN_DIR` points at a **patched** build of the mesh plugin rather than the stock one
built in [01 §3](01-cluster-setup.md#3-the-nccl-mesh-plugin):

```text
NCCL_MESH_PLUGIN_DIR=$HOME/glm3x/nccl-mesh-patched
```

The chat template is mounted read-only and passed to the engine:

```text
-v $HOME/glm3x/chat_template-a5b45eb4.jinja:/models/chat_template.jinja:ro
```

```text
--chat-template /models/chat_template.jinja
```

And the launcher waits for the host to settle before starting the container — see §2.5.

### 1.2 What the patched plugin is

Patches `0004`, `0005` and `0006` against `autoscriptlabs/nccl-mesh-plugin` at commit `19924dcc`.
They are documented, with their measurements, in the sibling recipe's
[NCCL mesh chapter](https://github.com/NNNtrance/GLM-5.3-Flash-EXL3-DGX-Spark/blob/main/docs/06-nccl-mesh.md),
and carried as a branch of a public fork,
[`NNNtrance/nccl-mesh-plugin`](https://github.com/NNNtrance/nccl-mesh-plugin) (`gb10-dual-link-ptrcuda`),
offered upstream as [pull request #59](https://github.com/autoscriptlabs/nccl-mesh-plugin/pull/59).
In one line each: `0005` makes link selection device-aware so a two-cable pair actually uses both
cables, `0006` advertises `NCCL_PTR_CUDA` and a flush so the transfer buffers stop bouncing through
host memory, and `0004` adds the `NCCL_MESH_MIN_RNR_TIMER` knob. Patch `0007` is **not** part of
this; it measured equal and was not adopted.

If your nodes are wired with one cable per pair, set `NCCL_MESH_LINKS_PER_PEER=1` and `0005` becomes
a no-op.

### 1.3 What the chat template is

`chat_template.jinja` from `zai-org/GLM-5.3-Flash` at commit `690b7052` (2026-09-04), 10,950 bytes,
sha256 `0c4099f3382d6c92700dfb99725025360966fd73032f0ecf32377c0d9e6309c5`. It is byte-identical to
the file shipped in the `zai-org/GLM-5.3-Flash-BF16` revision `a5b45eb4`, and — verified live — to
the copy that our own production checkpoint repository has carried since its 2026-09-04 sync, so
there is no tokenizer-compatibility question to answer: **the file can be taken from the checkpoint
repository you already downloaded.** `tokenizer.json` is untouched by that commit.

The on-disk copy this recipe originally shipped with is the 2026-08-27 template
(`f12e0fe1`, 10,644 bytes, md5 `91576a54`). Four things changed upstream, all tool-calling
correctness:

1. `{%- else -%}{{- content }}` became `{%- elif content is not none -%}{{- content -}}` — this is
   the fix for the literal string `None` leaking into the prompt on an assistant turn that carries a
   tool call.
2. String concatenation via `~` instead of `+`, so a non-string coerces instead of raising.
3. Four `break` guards in the tool-result reordering loops. We rendered both templates and diffed:
   **zero** effect on the rendered text.
4. A genuine indentation/nesting bug fix in the tool-response block — `{%- endfor -%}{%- else -%}`
   was closing at the wrong level.

The thinking/effort block and the `<think>` parsing are **byte-identical** between the two versions.
`<tools>` / `</tools>` are not added tokens in either.

---

## 2. What each lesson actually did

Arms are cumulative: each column adds one change to the one before it. All five boots were healthy
(234–250 s), no crash, no gate drop. Noise band, established from round-to-round spread in the same
session: **C1 ±4 · C2 ±6 · C4 ±9 · C6 ±6 · C8 ±3 %**.
`[measured-here, raw not published]`, 2026-09-05/06.

| Metric | baseline (production tree) | +NCCL 8 channels | +patched plugin | +template | +fadvise/trim |
|---|---|---|---|---|---|
| Probe gate cold / warm | 10/10 · 10/10 | 10/10 · 10/10 | 10/10 · 10/10 | 10/10 · 10/10 | 10/10 · 10/10 |
| Code gate cold / warm | 12/12 · 12/12 | 12/12 · 11/12 | 12/12 · 12/12 | 12/12 · 12/12 | 11/12 · 12/12 |
| Code gate, repeat run | — | not run | — | — | 12/12 |
| Empty content | 0 | 0 | 0 | 0 | 0 |
| Tool-call gate | not run | not run | not run | **8/8** | not run |
| C1 aggregate tok/s | 52.2 | 52.4 | 52.5 | 52.3 | 52.4 |
| C2 aggregate | 76.2 | 73.3 | 74.7 | 73.6 | 74.2 |
| C4 aggregate | 100.4 | 108.7 | 106.3 | 105.6 | 106.7 |
| C6 aggregate | 122.2 | 125.6 | 127.7 | 128.2 | 130.5 |
| C8 aggregate | 151.3 | 149.3 | 148.8 | 151.2 | 152.4 |
| C1 per-stream | 56.8 | 57.8 | 56.4 | 57.9 | 56.7 |
| C8 per-stream | 22.5 | 22.5 | 22.2 | 22.4 | 22.9 |
| TTFT C1 / C8 (s) | 0.381 / 1.033 | 0.375 / 1.027 | 0.377 / 1.033 | 0.384 / 1.036 | 0.380 / 1.036 |
| prefill 7k (tok/s) | 1,621 | 1,633 | 1,640 | 1,640 | 1,704 |
| prefill fresh (tok/s) | 1,822 | 1,832 | **1,933** | 1,936 | 1,948 |
| Acceptance % (C8) | 62.6 | 61.4 | 62.0 | 62.5 | 63.2 |
| Tokens per step (C8) | 5.38 | 5.29 | 5.34 | 5.37 | 5.42 |
| KV pool (tokens) | 3,455,072 | 4,344,927 | 4,353,623 | 4,350,724 | 4,359,420 |
| Per-rank equivalent fraction | 0.8168 | 0.8477 | 0.8477 | 0.8480 | 0.8474 |
| Boot (s) | 239 (dirty, upper bound) | 250 | 250 | 250 | 234 |

**Every decode difference in that table is inside the band, and the round-to-round spread (0.3–6.7 %)
is the same size as the arm-to-arm differences.** That is the finding, not a caveat to it. Reading
this table one column at a time is how we produced the wrong verdict corrected in §3.

### 2.1 NCCL channels — the verdict that had to be corrected

On the EXL3 line `NCCL_MAX_NCHANNELS=8` was worth +13 % at C8. The single arm above says C8 −1.3 %,
C1 +0.4 %, prefill equal — inside the band everywhere. We nearly dropped the setting on that basis.
See §3: it is worth **+7.7 % at C4 and +9.7 % at C6** and it stays.

One trap worth naming, because it is the reason the setting looked "already tried": an environment
file in our tree called `.env.exl3-ab-nccl8ch` was **not** a clean trial of this setting — it also
set `NCCL_PROTO=LL`. The arm above was the first clean one.

Also note the engine's NCCL version differs between our two lines (2.29.7 here, 2.30.7 there), so
the default channel count may not be the same; `NCCL_DEBUG=WARN` does not log it. The measurement
answers the question regardless of the mechanism.

**What this cost:** nothing. One environment line.

### 2.2 The patched mesh plugin — prefill only

Decode C1 through C8: equal. Prefill fresh: **1,832 → 1,933 tok/s, +5.5 %**, and it holds in the
candidate (1,938). The plan for patch `0006` predicted "prefill +5 %" and "C1–C8 +4–6 %"; the first
held, the second did not.

**The dual-cable gate passed**, so "decode did not speed up" is not "the second cable is idle".
`port_xmit_data` (×4 B) read over three minutes of C8 load:

| Pair | Cable 1 | Cable 2 | Split |
|---|---|---|---|
| head → worker-1 | 40.86 GB | 40.41 GB | 50.3 / 49.7 % |
| worker-1 → worker-2 | 40.91 GB | 40.46 GB | 50.3 / 49.7 % |
| worker-2 → head | 40.94 GB | 40.50 GB | 50.3 / 49.7 % |

All four ConnectX-7 ports ACTIVE / LinkUp / 200 Gb/s; the `.so` inside the container was
sha256-verified as the patched build. Decode does not speed up because **decode on this stack is not
fabric-bound** — [11 — Measured profile](11-measured-profile.md) puts NCCL at 13.8–15.1 % of a C1
step, and an independent all-reduce sweep (§4) shows the decode-sized collectives are latency-bound,
not bandwidth-bound.

**What this cost:** nothing measurable. KV +0.2 % (equal), gates full, no decode regression.

### 2.3 The chat template — the one clean win, and it is free

Speed, KV pool, acceptance and prefill: all equal. The **tool-call gate passes 8/8**, and two of
those eight are direct tests of what the template fixes — the `None` leak on an assistant turn
carrying a tool call, and out-of-order tool results. Gates pass cold **and** warm, so tokenisation
was not disturbed.

**What this cost:** nothing. This is the only unambiguous gain of the whole exercise, and it is a
10,950-byte file.

### 2.4 `posix_fadvise(DONTNEED)` + `malloc_trim` — did not carry over

KV pool 4,350,724 → 4,359,420, **+0.2 %**, where the same change was worth +4.1 % on the EXL3 line.
The reason is legible in the log: `malloc_trim` released **60 MB** of RSS here (3.64 → 3.58 GiB),
because the EXL3 line's eager safetensors loader leaves a ~2.7 GiB allocator arena behind and
`--load-format instanttensor` leaves none. There is nothing to give back.

The page-cache half did work — the cache for 47 + 1 shards was genuinely dropped and **MemFree** rose
on two nodes from 0 to 5 GiB — but **MemAvailable** did not move, because page cache already counts
as available. Prefill fresh 1,936 → 1,948 (+0.6 %, inside the band). Boot 250 → 234 s.

**What this cost:** nothing, and it is harmless, so it can be kept for the boot time. It is not a
memory lever on this line.

Two related transfers were **not** attempted for the same reason: `--enable-ep-weight-filter` and
per-rank load sidecars are both no-ops under `instanttensor`, which already loads the NVFP4 weights
in 57 s.

### 2.5 The settle gate — and the biggest finding of the night

The gate: after `docker rm -f`, wait up to 180 s for host `MemAvailable` to climb back over a
threshold before starting the engine. Our threshold is 112 GiB, which is 92 % of the 121.6 GiB the
nodes have. It runs **before any weight is loaded**, so it is a "is the host clean" test and does not
depend on the model's footprint.

Its measured effect is entangled with something larger, and this is the part worth reading:

**The KV pool number you read at boot depends on an estimator, not on a measurement.** The baseline
arm reported 3,455,072 tokens (28.40 GiB); all four later arms reported 35.80–35.89 GiB and
4.34–4.36 M tokens, agreeing with the 4,321,739 recorded on 2026-09-03 at the same fraction. The
baseline is the outlier. It splits into two terms:

| Term (head node) | baseline | later arms | Δ |
|---|---|---|---|
| CUDA-graph memory **estimate** | 0.88 → 0.8168 (≈7.69 GiB charged) | 0.88 → ~0.848 (≈3.9 GiB) | ≈3.8 GiB |
| Consumed (weights + non-torch) | 69.05 GiB | 65.28–65.42 GiB | ≈3.6 GiB |
| **Available for KV** | **28.40 GiB** | **35.80–35.89 GiB** | **≈7.4 GiB** |

The estimator charges 3.9 GiB against the budget on a typical boot — and 7.69 GiB on that one —
while the graph pool that actually gets allocated is 0.07–1.32 GiB. It varies about 2× from boot to
boot. `VLLM_MEMORY_PROFILER_ESTIMATE_CUDAGRAPHS` is not set anywhere in either tree; this is the
estimator's own behaviour, not a setting of ours. Turning it off was tried and **rejected** —
[12 §2](12-what-we-closed.md#2-the-cuda-graph-memory-estimator-flag).

The settle gate is the only difference between the two launchers that touches the "consumed" term,
so it is the natural suspect for the 3.6 GiB half. **But the baseline arm was also the first boot
after tearing down a different engine, so two causes point the same way and one boot cannot separate
them. The attribution is probable, not proven.** `[measured-here, raw not published]`

**The rule this produced, and it is now in our harness:** a KV pool column cannot be read without the
per-rank `equivalent to --gpu-memory-utilization` line printed beside it. A KV difference between two
arms **may not be attributed to the variable** unless those lines match. In the candidate they are
0.8481 / 0.8476 / 0.8480, so its KV comparison against the other gated arms is valid, and its
comparison against the baseline is **not**.

**What this cost:** up to 180 s of boot time in the worst case; in practice the gate cleared quickly
and the candidate booted in 234 s, faster than the baseline.

---

## 3. The correction: dropping the channel setting is not free

The candidate was originally going to drop `NCCL_MAX_NCHANNELS=8` on the strength of the single arm
in §2. A pooled comparison across the arms that had it against the arms that did not said the
opposite, and it was then confirmed by a **single-variable A/B inside one session, same plugin, same
boot conditions**:

| Concurrency | 8-channel arms, pooled | no-8-channel arms, pooled | Δ % | Band % | Same-session A/B | Δ % |
|---|---|---|---|---|---|---|
| C1 | 52.4 | 50.8 | −3.1 | 4 | 51.2 → 51.7 | +1.0 |
| C2 | 74.2 | 73.6 | −0.8 | 6 | 73.7 → 72.0 | −2.3 |
| C4 | 106.2 | 99.8 | −6.0 | 9 | 99.5 → **107.2** | **+7.7** |
| C6 | **128.8** | **117.5** | **−8.8** | 6 | 115.5 → **126.7** | **+9.7** |
| C8 | 150.8 | 146.4 | −2.9 | 3 | 147.5 → 148.0 | +0.3 |
| prefill 7k | — | — | — | — | 1,646 → **1,699** | +3.2 |
| prefill fresh | — | — | — | — | 1,907 → 1,938 | +1.6 |

Five concurrency levels out of five move the same way, and C6 is outside the band in both readings.
**The setting stays in production.** `[measured-here, raw not published]`, 2026-09-06.

The lesson is about method, not about NCCL: **a one-arm reading at the endpoints missed the middle.**
C1 and C8 are where we habitually look, and this effect lives at C4 and C6.

A side observation we can support but not explain: the candidate's code gate passed 12/12 both cold
and warm, while the two arms without the channel setting each dropped the same item. Channel count
changes the reduction order inside an all-reduce, and at temperature 0 a token on a knife edge can
flip. That is **consistent with** the observation; it is not evidence for it, and it was not measured.

---

## 4. Fabric facts that apply to any recipe on this hardware

Measured with an in-house `torch.distributed` all-reduce microbenchmark built to reproduce
`nccl-tests`' contract (geometric sweep 8 B → 64 MiB ×2, 10 warm-up + 50 timed iterations per size,
`busBW = algBW · 2(n−1)/n` for n = 3, plus a fixed-pattern correctness check that passed 132/132),
in the production transport configuration (`NCCL_ALGO=Ring`, `NCCL_MAX_NCHANNELS=8`, patched plugin).
Median of 3 repetitions. `[measured-here, raw not published]`, 2026-09-06.

| Size | Time (µs) | algBW (GB/s) | busBW (GB/s) |
|---:|---:|---:|---:|
| 8 B | 80.38 | 0.0001 | 0.0001 |
| 4 KiB | 72.32 | 0.057 | 0.076 |
| 8 KiB | 74.68 | 0.110 | 0.146 |
| 64 KiB | 86.40 | 0.759 | 1.011 |
| 128 KiB | 172.48 | 0.760 | 1.013 |
| 1 MiB | 275.12 | 3.811 | 5.082 |
| 4 MiB | 305.51 | 13.729 | 18.305 |
| 16 MiB | 1,096.79 | 15.297 | 20.396 |
| 64 MiB | 3,954.98 | 16.968 | 22.624 |

Two conclusions, both format-independent:

- **Decode is latency-bound.** Time is flat within about 20 % across a 4,096× size range (8 B to
  32 KiB, 72–85 µs). Nothing on the software side is going to move that; it is the cost of the
  round trip.
- **Prefill is at the wire.** A 16.8 MB collective — which is what a 2,048-token chunk at
  hidden 4,096 in bf16 produces — measures 20.40 GB/s against a measured per-pair wire of
  20.8 GB/s, **98.1 %**. There is no software headroom left there either.

The ceiling itself is **PCIe Gen5 x4 per NIC, about 15 GB/s per card**, not the cable's rated speed.
An earlier figure of ours that used the cable rating as the denominator has been withdrawn.
The band where channel count is decisive is 128 KiB – 16 MiB, which is why §3 shows up at C4/C6 and
not at C1.

---

## 5. Candidate against the earlier production configuration

Both at `gpu-memory-utilization` 0.88, same image, same checkpoint, same draft, same prompts.
`[measured-here, raw not published]`, 2026-09-05/06.

| Metric | production (as shipped in [03](03-launch-and-flags.md)) | candidate | Δ |
|---|---|---|---|
| Probe gate cold / warm | 10/10 · 10/10 | 10/10 · 10/10 | equal |
| Code gate cold / warm | 12/12 · 12/12 | 12/12 · 12/12 | equal |
| Empty content | 0 | 0 | equal |
| Tool-call gate | not run | **8/8** | new |
| MMLU sample (57 × 35 ≈ 2,000 q) | — | 86.52 ± 0.74 | — |
| Needle probe | 20/20 to 997,952 tokens (2026-09-03) | 6/6 at 40k / 80k prompt tokens | not the same test |
| C1 aggregate tok/s | 52.2 | 51.7 | −1.0 %, inside band |
| C2 aggregate | 76.2 | 72.0 | −5.5 %, inside band |
| C4 aggregate | 100.4 | 107.2 | +6.8 %, inside band |
| C6 aggregate | 122.2 | 126.7 | +3.7 %, inside band |
| C8 aggregate | 151.3 | 148.0 | −2.2 %, inside band |
| C1 per-stream | 56.8 | 56.6 | equal |
| C8 per-stream | 22.5 | 22.5 | equal |
| TTFT C1 / C8 (s) | 0.381 / 1.033 | 0.384 / 1.046 | equal |
| **prefill 7k (tok/s)** | 1,621 | **1,699** | **+4.8 %** |
| **prefill fresh (tok/s)** | 1,822 | **1,938** | **+6.4 %** |
| Acceptance % (C8) | 62.6 | 62.2 | equal |
| Tokens per step (C8) | 5.38 | 5.35 | equal |
| KV pool (tokens) | 3,455,072 (see caveat) | 4,347,826 | not comparable |
| Per-rank equivalent fraction | 0.8168 | 0.8481 / 0.8476 / 0.8480 | — |
| Swap under load, 3 nodes | truncated ruler (see caveat) | 0.03 / 0.00 / 0.03 GiB | — |
| MemAvailable min under load | 3–4 GiB | 4.83 / 7.19 / 7.17 GiB | roomier |
| Boot to serving | 239 s (dirty, upper bound) | 234 s | equal |

**The honest summary: on decode this candidate is the same engine.** What it buys is prefill
(+5–6 %), a tool-calling correctness fix that the gate can see, and a boot that starts from a clean
host. What it costs is one patched plugin build and one mounted file.

**KV caveat.** The two KV rows are not comparable — the per-rank equivalent fractions differ (0.8168
against ~0.848), which is exactly the condition under which §2.5's rule forbids the attribution. The
candidate's 4,347,826 agrees with the 4,321,739 measured at the same fraction on 2026-09-03; the
baseline column is the outlier of the five boots. Do not read the KV column of this table as a gain.

**Needle caveat.** The two needle rows are not the same test and no conclusion should be drawn from
the pair. The full 20/20 grid to 997,952 tokens
([`results/needle/`](../results/needle/needle-in-a-haystack-report.md)) is from 2026-09-03 and was
**not** repeated on the candidate; the 6/6 here is a light probe at 40k and 80k prompt tokens, run
only to confirm that long-context retrieval had not broken.

**Ruler caveat, and it retroactively affects earlier tables.** The `MemFree` and `swap` columns of
the first five arms were **truncated to integers**: a remote `awk` printed `1,00` under a
comma-decimal locale and the collecting `awk` cut it at the comma and read `1`. So a "0.00 GiB swap"
row in an early table actually meant "less than 1 GiB". The sampler now runs under `LC_ALL=C`, at a
10 s period, and records `MemAvailable` and `Cached` as well. The candidate's swap figures above are
from the corrected ruler.

One more artefact of the same kind, recorded so nobody chases it: a `swap_max` of 1.33 GiB in one
arm's raw file is the **first sample** — the previous container being torn down. It falls to
0.024 GiB within 60 s and stays there. Steady-state values are what the tables report; the raw file
is unedited.

---

## 6. Measurement-protocol corrections made during this work

Both of these change how earlier numbers on this line should be read.

**Prompt rotation.** The old sweep runner queued `max(8, 4·C)` requests as `prompts[i % 12]`, so C1
and C2 only ever saw the first 8 prompts while C8 saw all twelve. The prompt set is heterogeneous
(code / prose / math / JSON) and both speed and draft acceptance depend strongly on content type, so
the concurrency levels were being compared **on different work**. On the sibling line this produced a
false "acceptance −2.4 points" alarm. The corrected runner turns the list a whole number of times at
every level: C1 12, C2 12, C4 24, C6 24, C8 36 requests.

**Warm-up rounds.** The sibling line's rule is "discard the first two sweep rounds". On this line it
is **stale**: across all five arms the difference between the warm-up round and the median was inside
the band (C1 −0.5…+3.5 %, C8 −0.9…+1.1 %). We run one warm-up plus three measured rounds and take
the median, and every round is printed.

**Post-boot drift.** Single-stream C1 on this stack drifts **upward for roughly 30 minutes after a
boot** — rounds at 03:51 gave 49.4–50.8, rounds after 04:04 gave 51.7–52.9. It cost us a wrong
verdict once ([12 §4](12-what-we-closed.md#4-cpu-idle-states)). Two rules came out of it: compare C1
between arms only at the **same boot age**, and run every system-setting A/B with a **return leg**.

**Gate rule.** Probe 10/10 and empty-content 0 are hard failures. The code exam is expected at 12/12,
but a single miss triggers **one repeat**; only if the repeat is also short does the arm fail, and
which problem failed is recorded. The item that flickers is always the same one (`matrix`/spiral) and
it is documented in [06 — Benchmarks](06-benchmarks.md) as a known sampling-noise item, not a quality
defect.

---

## 7. What this exercise cost

Six engine boots, about seven hours of cluster time across two sessions, one measurement lock held
end to end. No checkpoint, sidecar or image was deleted; `gpu-memory-utilization` was never changed;
the production tree was never written to — it was the control arm. The only system settings touched
were CPU idle states, which were changed for one A/B and restored (verified file by file), and a GPU
clock lock, which was released and confirmed released.

The honest ledger: of five lessons carried over, **one was a clean gain** (the template, free),
**one was a prefill gain** (the plugin, +5.5 %), **one was nearly discarded on a bad reading and is
worth +8–10 % in the middle of the concurrency range** (the channel setting), **one did essentially
nothing but is harmless** (fadvise/trim), and **one cannot be cleanly attributed** (the settle gate).
Two verdicts had to be corrected, and one measurement instrument turned out to be lying about a whole
column.
