# 09 — Open problems, and what we retracted

Two lists. The first is what we did not solve: the symptom, everything we eliminated, and what the
next experiment would be. The second is claims we made and later withdrew — not corrected, withdrawn,
with the reason, so that nobody builds on them.

Branches we deliberately closed are in [08 — What we tried and rejected](08-what-we-tried.md).
Accepted settings are in [03 — Launch and flags](03-launch-and-flags.md); numbers are in
[06 — Benchmarks](06-benchmarks.md), [07 — Speed](07-speed.md) and
[05 — Memory ladder](05-memory-ladder.md).

Evidence tiers are the ones from the style guide, plus `[measured-here, raw not published]` for
things we measured on this cluster whose raw file is not in [`results/`](../results/).

---

## Open problems

| # | Problem | Impact | Tier | Date |
|---|---|---|---|---|
| 1 | Rank 0 loads weights ~3× slower than the workers | ≈ 43 % of a boot in the pin era; ~5 min boot today | `[measured-here, raw not published]` | 2026-08-31 |
| 2 | One node is ~2.5 % slower, permanently; and its fan does not spin at idle | The slowest node sets the cluster's pace | `[measured-here, raw not published]` | 2026-08-29 |
| 3 | The draft model's page layout costs 26–35 % of the KV pool | Concurrency at 1M context | `[measured-here]` | 2026-09-03 |
| 4 | Speculative decoding flips near-ties at temperature 0 | Reproducibility, not accuracy | `[measured-here, raw not published]` | 2026-09-03 |
| 5 | The b12x MoE + EP branch has no numerical unit test | Blocks reopening a closed branch | `[not tested]` | 2026-09-03 |
| 6 | The TP=3 pad-then-narrow loader does not cover quantized draft weights | Blocks the MXFP8 draft | `[measured-here, raw not published]` | 2026-09-03 |
| 7 | marlin drops the checkpoint's W4A4 activation scales; the quality cost is unmeasured | Unknown quality debt | `[not tested]` | 2026-09-03 |
| 8 | `--max-num-batched-tokens` 2048 against 4096 was never A/B'd on this stack | Possibly leaving prefill or pool on the table | `[not tested]` | 2026-09-03 |
| 9 | `--reasoning-parser deepseek_r1` against `glm45` was never A/B'd on this stack | Two official sources disagree | `[not tested]` | 2026-09-03 |
| 10 | A single C4 json speed reading at 0.88 is 11 % low and unverified | Might be nothing; might be a real cost of 0.88 | `[measured-here]` | 2026-09-03 |
| 11 | IFEval was not cross-checked against lm-eval's own `ifeval` task | Our 78.9 % may understate the model | `[measured-here]` | 2026-09-03 |
| 12 | ExtractBench was never run on this build | A quality dimension we cannot report | `[not tested]` | 2026-09-03 |
| 13 | Prose acceptance is ~13 %; we do not know why | Prose runs at speculation-off speed | `[measured-here]` | 2026-09-03 |
| 14 | The KV allocator warns it pads 10 layers and may waste up to 29.4 % | Possibly a large unclaimed pool | `[measured-here, raw not published]` | 2026-09-03 |

### 1. Rank 0 loads weights about 3× slower than the workers

**Symptom.** Three identical machines doing identical work, each timed from its own log:

| Node | Weight load |
|---|---|
| head (rank 0) | 488.42 s |
| worker-1 | 171.23 s |
| worker-2 | 160.48 s |

The workers finish and then wait five minutes for the head. In the NVFP4 era that was 43 % of a
12 min 39 s boot.

**Everything we eliminated, all measured.** Disk throughput (2.9 GB/s on all three, `dd iflag=direct`
on 4 GiB), file fragmentation (29 / 24 / 20 extents — a 45 % spread does not produce a 3× effect),
free memory at load time (43.4 / 44.7 / 44.6 GiB), core count and load average (20 cores, ~0.85 on
all three), and bytes loaded (exactly 63.48 GiB on all three). **Swap was our first diagnosis and it
is disproved** — see the retraction below.

**What is left.** The effect follows the **role**, not the machine: two independent boots gave 488.42 s
and 483.96 s. The only known asymmetry is that rank 0 also carries the `APIServer` and `EngineCore`
processes (5 processes in the container against 4 on a worker). Why that produces a 2.6–3× slowdown
on 20 cores is unexplained; CPU contention does not account for that magnitude.

**The experiment that would settle it**, and why we have not run it: move rank 0 to another node. If
the slowness follows the role, it is architectural; if it follows the machine, it is that unit. The
cost is that the API address moves, which breaks every script and the registry, so it has to be a
separate, deliberate piece of work.

**Status on this stack.** The production boot is about 5 minutes and the weight load is 57 s, so the
absolute cost is now small; **we have not re-measured whether the ratio still exists.** Raw not
published.

### 2. One node is about 2.5 % slower, permanently, and its fan does not spin at idle

Two separate phenomena that were initially confused for one. Both were measured after all three nodes
were brought to identical firmware (SBIOS, EC and SoC images all matched).

**The fan.** One unit idles 8–10 °C hotter than the other two. Root cause: the board vendor's embedded
controller drives the idle fan from **power draw, not temperature**. That unit's idle draw sits below
the threshold, so the fan never starts, so it heats up. The proof is a cooling experiment: after a
CPU load ended, with the fan still spinning, the board sat at 38 °C — **colder than its own idle** —
and climbed back to 46 °C once the fan stopped, while the control node never moved off 35 °C. This
matches a publicly reported case on identical hardware. Suggested workaround (from that report,
`[reported]`): attach a USB device drawing 5 W or more to push idle draw over the threshold.
**We have not applied or verified it.**

**The speed deficit is not thermal.** Brought to the same starting temperature with fans already
spinning, that node still ran 62 MHz below the fastest one and about 2.5 % slower on compute, with
peak temperatures within 2 °C of the control. Sustained clocks: 2502 / 2485 / 2440 MHz. Memory
bandwidth is identical on all three (225–231 GB/s).

**Hypothesis, not conclusion: silicon binning.** A 2.5 % spread is ordinary part-to-part variation,
and the middle node sits between the other two rather than the distribution being bimodal. We have
not proved it and there is nothing to do about it if it is true. The operational consequence is real
and belongs in any three-node plan: **the slowest node sets the pace of the cluster**, so a mixed
configuration (one node clock-capped, two not) is the worst of both worlds. Raw not published.

### 3. The draft model's page layout costs 26–35 % of the KV pool

**Symptom.** With the DFlash2 draft attached, the KV pool at `gpu-memory-utilization 0.85` is
3,860,869 tokens; with speculation off on the same arm it is 5,934,911. That is **−35 %**. Measured
against the eager arm's 4,365,217 the same reference gives −26 %, but those two are not a
single-variable pair, so treat 26–35 % as the range rather than either endpoint as the answer.

**What we know about the cause.** It is block identity, not bytes. The draft group's page does not
divide the manager's page, so the draft blocks stay padded — in the EXL3 branch we measured the draft
block at 5.9 % occupancy, costing roughly 11 % of the pool per 1M-token request (14 % with async
scheduling). The main model costs 512 B per token, the draft 1024 B.

**What we tried and why it did not work:** fp8 draft KV in three variants and a split page layout —
all four are written up in [08 — What we tried](08-what-we-tried.md), items 9 and 10.

**The known fix and its risk.** Make the kernel block equal the manager block (in the EXL3 branch the
equivalent change would have taken the draft block to 93.7 % occupancy and the pool up about 11 %).
The risk is not a crash: if the runner's strided view is not corrected at the same time, the result is
**silent KV corruption**. Any attempt has to be gated on the correctness probe and the code exam, not
on the engine starting.

**What we recovered instead.** The memory ladder, which bought +11 % — see
[05 — Memory ladder](05-memory-ladder.md) and [`results/memory-ladder/`](../results/memory-ladder/).

### 4. Speculative decoding flips near-ties at temperature 0

**Symptom.** With exact greedy verification, a draft model cannot change the output at all. Ours does.
Swapping only the draft checkpoint (same target, same stack) made one task — generating a spiral
matrix — fail deterministically in two consecutive repeats, while the older draft and the
speculation-off arm both get it right. Separately, in the tool-eval-bench battery at temperature 0,
**9 scenarios changed outcome from trial to trial** across 8 trials.

**What we think is happening.** The logits produced by the target model's verification pass shift
slightly with the batch shape (which depends on the acceptance pattern), so where two candidate
tokens are nearly tied, the argmax flips. Plausible sources: atomic accumulation order in the marlin
MoE kernel, or batch-shape-dependent kernel selection. **This is a hypothesis; it has not been
tested.**

**Severity.** It is instability at noise level, not a quality defect: quality gates are stable
(12/12 ×3, 10/10, and the benchmark spread across 8 trials is ±0.9 points). It matters if you need
reproducible output.

**Workaround that does work.** The speculation-off arm is bit-identical across runs
([08 — What we tried](08-what-we-tried.md), item 12), at roughly one third of the speed.

**What would settle it.** Log the top-2 logit gap on the verification pass for accepted and rejected
positions, and check whether flips correlate with the gap; and re-run the same task with the MoE
backend forced to a deterministic reduction order. Neither has been done. Raw not published;
per-trial reports are in [`results/tool-eval-bench/`](../results/tool-eval-bench/).

### 5. The closed b12x MoE + EP branch has no numerical unit test

The branch is closed because it produced corrupt output (code exam 0/12) — but its CPU unit test
passed 6/6, which means the test did not test the thing that broke. Until there is a **GPU-side
numerical comparison against a reference implementation, per layer**, that branch cannot be reopened
responsibly. Writing that test is the actual open work; the patch itself already exists in
[`patches/ep-patch/`](../patches/ep-patch/). `[not tested]`, 2026-09-03.

### 6. The TP=3 loader does not cover quantized draft weights

Our pad-then-narrow loader pads only the BF16 weight paths. An MXFP8 draft goes through the
weight-plus-scale path, which the patch never touches, so rank 2 dies with
`start (768) + length (384) exceeds dimension size (1024)` on the draft's `k_proj`/`v_proj`
(8 KV heads × 128 = 1024 rows). The fix is understood and small; the prize (about 1 GB of weights per
node) did not justify it. `[measured-here, raw not published]`, 2026-09-03. Details in
[08 — What we tried](08-what-we-tried.md), item 8.

### 7. marlin drops the checkpoint's activation scales, and the cost is unmeasured

marlin is the only MoE backend in this fork that accepts expert maps, which is what makes TP=3 plus
expert parallelism possible at all. It is weight-only, so the checkpoint's W4A4 activation scales are
dropped and the experts run A16. **We do not know what that costs in quality.** The only route we had
to measure it was the b12x MoE + EP branch, which produces corrupt output, so the comparison arm does
not exist. This is a known, unmeasured debt and it is stated here rather than hidden: every quality
number in [06 — Benchmarks](06-benchmarks.md) was produced *with* this fallback in effect, so they are
valid for this recipe as shipped — they just do not tell you what the checkpoint could do.
`[not tested]`, 2026-09-03.

### 8. `--max-num-batched-tokens` 2048 against 4096

We run 2048. The lab recipe our fork derives from uses 4096. On the NVFP4-era stack, 8192 was measured
and rejected decisively (pool −28 %, no speed gain, and a lock-up when combined with a KV pin), but
**4096 was never tried, and 2048 against 4096 has never been run as a single-variable A/B on this
stack.** The 8192 result does not settle the midpoint. `[not tested]`, 2026-09-03.

### 9. `--reasoning-parser deepseek_r1` against `glm45`

Two official sources disagree: the model card says `deepseek_r1`, the vendor recipe site says `glm45`.
We follow the model card. On the NVFP4-era stack we did measure that the parser choice was **not** the
cause of the empty-content phenomenon (8/10 empty responses persisted after switching), but a
quality/behaviour A/B on this stack has not been run. `[not tested]`, 2026-09-03.

### 10. One C4 json speed reading at 0.88 that we could not explain

In the memory ladder, the C4 json category read **80.1 tok/s at 0.88** against 89–91 at every other
step (0.85, 0.86, 0.87 and 0.89 all landed in the normal band). It is a single reading taken once, and
C4 code and C1 were normal in the same pass, so the most likely explanation is noise. It was never
re-measured, and 0.88 is the production setting. If you see C4 json below 85 on your cluster, this is
the one number in our tables that we do not vouch for. `[measured-here]`, raw in
[`results/memory-ladder/`](../results/memory-ladder/), 2026-09-03.

### 11. IFEval was not cross-checked against lm-eval

Our 78.9 % prompt-strict was produced by a third-party agent harness using **its own evaluator**, and
we found at least one case where the evaluator looks wrong: a response that did contain a title in
`<<...>>` form was scored as failing the "title" constraint. Strong open models sit at 85–90 on this
benchmark, so our number is 6–10 points below the band, and part of that gap may be the evaluator
rather than the model. The cross-check is about an hour of runtime against lm-eval's official
`ifeval` task and it was not done. Until it is, **treat 78.9 % as a lower bound with a known
instrument problem.** `[measured-here]`, report in [`results/ifeval/`](../results/ifeval/),
2026-09-03.

### 12. ExtractBench was never run on this build

The document-extraction benchmark we used during the NVFP4 era was never re-run on the production
build; the run was skipped because it takes over two hours and the operator was at the keyboard when
its slot came up. The only numbers we have are from the previous checkpoint and previous stack
(Short 93.93; 94.51 on the fair 215-document intersection against 96.46 for an H100 FP8 reference),
and they are **not comparable** to this build. There is also a structural limit worth recording: this
engine's encoder budget is 32,242 tokens and the benchmark averages 2,837 tokens per page, so at most
11 pages fit in one request — the Medium and Long splits are physically impossible here, whatever the
stack. `[not tested]` on this build, 2026-09-03.

### 13. Prose acceptance is about 13 % and we do not know why

Category speed test, warm, C1, effort low:

| Category | C1 decode tok/s | acceptance |
|---|---|---|
| prose | 20.6 (16.6–24.6) | 13.3 % |
| code | 43.0 (30.9–56.3) | 44.2 % |
| math | 57.0 (47.0–74.7) | 60.3 % |
| structured json | 52.1 (42.3–59.0) | 55.3 % |

At 13 % acceptance the draft is contributing almost nothing and prose runs at roughly the
speculation-off speed. Candidate explanations — the draft's training distribution, the fact that half
those prompts were not in English, or simply that creative text is high-entropy — were **not
measured**, and the prompt set needs to be all-English before the number is comparable to anyone
else's. `[measured-here]`, raw in [`results/speed/`](../results/speed/), 2026-09-03.

### 14. The KV allocator says it may be wasting up to 29.4 %

At every boot the engine logs that it added 10 padding layers and "may waste at most 29.41 %". We
never went through the group layout (MLA, KDA and draft layer counts) to find out how much of that
warning is real. If a meaningful part of it is, it is a larger lever than anything in the memory
ladder. Nobody has looked. `[measured-here, raw not published]`, 2026-09-03.

---

## Retracted

These are claims this project made and has withdrawn. They are not being restated in corrected form
where the original measurement cannot be reproduced — a withdrawn number stays withdrawn.

### R1 — "Single-stream speed is low because of the 8-token draft verification step"

**Claimed** on 2026-09-03, after the speculation-off arm scored 12/12 four times while the
speculative arm wobbled: we named the draft path's `q_len=8` verification step as the prime suspect
for both the quality wobble and the C1 deficit.

**Withdrawn** the same night. The cause was the b12x MLA **decode** kernel's 22-head path — a shape
the upstream lab never tested, because it tests TP=2 and TP=4 (32 and 16 heads). Padding decode and
extend to 24 heads fixed quality, acceptance and speed at once: C1 went 48.3 → 56.9 tok/s, acceptance
44–53 % → 62–65 %, code exam 9/12 → 12/12 ×3. The 8-token verification shape is real and it is still
what makes the engine's FULL graph mode unreachable, but it was not the cause of the C1 deficit and
naming it sent the next session looking for the wrong fix (removing the draft).

### R2 — "The memory-fraction ceiling on this platform is 0.883"

**Claimed** 2026-09-01, measured honestly: with the KV pool pinned, the driver holds about 14.2 GiB
persistently, `--gpu-memory-utilization 0.90` was refused by the precheck (109.46 GiB requested
against 107.54 free), and 0.883 was where the arithmetic ran out. It then hardened in our own notes
into a rule — at one point written as "above 0.85 is forbidden", which was never what the measurement
said.

**Withdrawn** 2026-09-03. Without the KV pin, device-side free memory is 111.3–111.8 GiB, i.e. a
device ceiling of about **0.915**. The binding constraint is not the device at all: it is host memory
and swap on the head node. Production runs 0.88 with 439–456 MB of swap on the head; 0.89 doubles
that swap and was rejected for that reason, not because the device refused it. See
[05 — Memory ladder](05-memory-ladder.md).

### R3 — "Eager is faster; CUDA graphs give nothing on this platform"

**Claimed** repeatedly, and with what looked like good evidence. A published three-node GB10 report
(`tonyd2wild`, same model, native MTP-4) measured graphs at −1.2 % speed and −4.6 % pool, and stayed
eager for that reason. Our own local A/B on a 20 GiB test bed agreed
(differences inside a ±4 % band). The EXL3 branch A/B also rejected graphs (no gain, pool −4.2 %). We
closed the topic and wrote "do not reopen".

**Withdrawn for this stack**, 2026-09-03: on the production build, full graph capture is worth
**+22 % on single stream** (47.1 → 56.9 tok/s), at a cost of 12 % of the KV pool. Production runs with
graphs on.

**Two things were wrong, and the second is the more useful one.** First, the earlier local A/B was
measuring eager against eager without knowing it — zero graphs were captured, and the engine said so
in a warning we had not read. The right answer for the wrong reason. Second, the reason capture was
empty was the **draft head count** (22 % 4 ≠ 0 blocks the attention backend's graph support, and the
minimum across groups poisons the whole engine), not "speculative decoding makes the shape dynamic",
not "the hybrid attention stack is rejected", not "there is no flag for it", and not "there is nothing
to capture" — four claims we published and all four were wrong. Once the head padding landed, capture
works.

**Still standing:** `cudagraph_mode=PIECEWISE` is not to be opened, for a documented upstream reason
([08 — What we tried](08-what-we-tried.md), item 21), and eager remains a defensible choice if you
want the pool.

### R4 — "The harness explains our Terminal-Bench gap"

**Claimed** 2026-09-03 while reading a third-party comparison chart at small size: we read the model's
score as far below its peers and hypothesised that our harness and our low reasoning effort explained
the gap.

**Withdrawn** the same evening. We misread the chart. The correct values in that table are 86 / 84 /
74 for the three models compared, not the 64 / 69 / 54 we read off it. There is no gap of the kind we
were explaining, so both the reading and the explanation are withdrawn. Two further caveats stand:
the model identities in that table were never verified by us, and **we have not run Terminal-Bench
ourselves** — see [06 — Benchmarks](06-benchmarks.md) for what we did run.

### R5 — "Thinking can be turned off with `enable_thinking=false`"

**Claimed** 2026-08-30 with a measurement showing 61 tokens against 137, a 55 % saving. Withdrawn: the
measurement was taken against a **different publisher's checkpoint** whose chat template did contain
that switch. The template we serve does not contain the string at all, and passing the flag only
disables the extraction filter, so reasoning leaks into the answer. The saving number is withdrawn
rather than re-stated, because the run it came from cannot be reproduced on this stack. See
[08 — What we tried](08-what-we-tried.md), item 17.

### R6 — "Maximum reasoning effort costs about 20× the tokens"

**Withdrawn** 2026-09-03 and replaced by a measurement: the low-to-max token ratio is 4.6× to 18×
depending on the task (mean about 6×), and wall-clock 5–12×. The 18× case is an easy tool call where
the absolute numbers are 8 tokens against 144, so the ratio is large and the cost is trivial. The
original "20×" was an estimate presented as a fact. `[measured-here]`, raw not published, 2026-09-03.

### R7 — "The head node's slow weight load is caused by swap"

**Claimed** 2026-08-31 from a counter that showed the head node had read 13,156,219 pages from swap
against about 1.2 million on the others — 11.4×. We acted on it, cutting the KV pin by 2 GiB and
doing a clean reboot of all three nodes.

**Withdrawn**, because the fix changed nothing: 488.42 s → 483.96 s, and after the clean reboot swap
activity was **equal** across the three nodes (one worker actually swapped more than the head). The
cost of the wrong diagnosis was 5.4 % of the KV pool for zero return.

**The methodological lesson is the reason this entry exists.** That 11.4× was a cumulative counter
covering a three-hour window, and inside that window one experiment had locked the head node
specifically. We verified that the machines had booted at the same moment, and did not verify that
the same things had happened to them since. **A cumulative counter (`/proc/vmstat`, `/proc/diskstats`)
may only be compared across machines if you can show they went through the same events in that
window.**

### R8 — "The doubling defect is a language problem, and low effort suppresses thinking"

Both withdrawn 2026-08-31. The doubled output ("392392", "Paris.Paris") was reasoning leaking into
the answer, and it appeared with speculation on and off alike (9 of 30 both ways, so speculation was
innocent too). The root cause was in our own launcher: whenever `REASONING_EFFORT` was set, it forced
`enable_thinking` to false. That single line determined the conditions of every comparison run made
that day, so **all speed and quality numbers taken before 2026-08-31 were measured with thinking
off** and are not comparable with anything in this repository.

---

## Superseded, not retracted

Two classes of number in our history are valid but not comparable with what this recipe publishes.
They are listed so nobody tries to line them up.

- **Pin-era numbers.** Anything measured with `--kv-cache-memory` pinned (the whole NVFP4 era) has a
  larger KV pool and no activation headroom. Pool sizes from that era cannot be compared with the
  memory ladder in [05](05-memory-ladder.md).
- **Pre-2026-09-02 speed numbers.** The earlier code prompt set was deleted during a cleanup, so the
  NVFP4-era speed tables cannot be compared with the 12-prompt set that produced every speed number
  in [07 — Speed](07-speed.md). Only the newer set is documented there; the older numbers have no
  reproducible prompt set behind them any more.
