# Audit — check your install against ours

You have followed the recipe and the engine answers. Does it answer *correctly*,
and at the speed this hardware should give? Run the audit and compare.

```bash
audit/run-audit.sh
```

It takes about 25 minutes end to end (the category-speed step is most of it).
To run one section at a time:

```bash
audit/run-audit.sh health kv probe
```

Sections: `health` `kv` `probe` `code` `c1` `category` `memory`.

The script prints numbers; it does not grade them. Grading is this page.

## Settings every number below assumes

| | |
|---|---|
| Image | `harem/glm53-lil:t10` |
| Parallelism | TP=3, expert parallelism on, MoE backend `marlin` |
| Quantization | NVFP4 (`modelopt_mixed`), non-quantized layers BF16 |
| KV | dtype `fp8`, no pin, block size 256 |
| CUDA graphs | ON (with AOT compile), `--enforce-eager` absent |
| Speculative | DFlash2, k=7, draft TP 1 |
| Memory | `--gpu-memory-utilization 0.88` |
| Batching | `--max-num-seqs 8`, `--max-num-batched-tokens 2048` |
| Sampling | temperature 0, thinking ON, reasoning effort `low` |
| Date | 3 September 2026 |

Change any row and the expected values change with it. In particular: turning
CUDA graphs off costs about 22% at concurrency 1 and gives back about 12% of the
KV pool, and dropping to `--gpu-memory-utilization 0.85` shrinks the pool by
about 10%.

## What to expect

### health

`GET /health` returns 200 immediately once the engine is serving. Only the head
(rank 0) serves the API; the workers have no HTTP endpoint at all.

### KV pool — `[measured-here]`

Read from the engine's own start-up log line `GPU KV cache size: N tokens`.

| gpu-memory-utilization | KV pool (tokens) | Concurrency at 1M context |
|---|---|---|
| 0.85 | 3,881,159 | 3.88× |
| 0.86 | 4,023,188 | 4.02× |
| 0.87 | 4,156,521 | 4.16× |
| **0.88 (production)** | **4,310,144**, and **4,321,739** on the post-reboot production verification | 4.31× |
| 0.89 (rejected) | 4,408,695 | 4.41× |

Source: memory ladder measured 3 Sep 2026, plus the production verification run
after the three-node reboot the same night. The arithmetic that ties them
together: KV costs about 8.6 KB per token (35.98 GiB ↔ 4.37M tokens).

A pool within a few percent of ours is a match. A pool 30% or more below it is
not noise — the usual causes are a different drafter (the draft model's page
layout costs us about 35% of the pool as it is), `--max-num-batched-tokens`
raised to 8192 (that alone costs 28%), or `--block-size` left at a large value.

0.89 was rejected even though its pool is larger: at 0.89 the head swaps 927 MB
and its "free memory" is manufactured by pushing the machine onto disk. The
device ceiling is around 0.915; the binding limit is host memory, not the GPU.

### correctness probe — expect 10/10, empty-content 0 — `[measured-here]`

Ten short questions with checkable answers plus one long paragraph checked for
repetition locks. Our production configuration passes 10/10 on every start, and
we have repeated it after every configuration change.

This gate is not cosmetic. A decode kernel that silently computes the wrong
thing for our head count produced fluent, confident nonsense here while the
engine reported no error at all. If you score below 10/10 with a warm engine,
stop and find out why before you measure anything else.

`empty-content` counts answers the model put entirely in its reasoning field,
leaving `content` empty. Without a system line instructing the model to always
write a final answer outside its reasoning, we measured about 11% of simple
questions coming back with empty content (4 of 36); with that line, 0 of 100.

### code exam — expect 12/12 — `[measured-here]`

Twelve programming tasks; the model's code is executed and its assertions
checked. Production scored 12/12 three times over (gate plus two repeats), and
12/12 four times with speculative decoding disabled, deterministically.

For contrast, arms built without our head-padding patches scored 7/12 to 10/12
on the very same exam. That spread is what this gate is for.

Note it runs model-written python locally, in a temp file, with a 25-second
timeout and no sandbox.

### cold/warm C1 — `[measured-here]`

Same code prompt three times: the first cold, the next two warm.

**Read this before comparing.** Our published cold/warm numbers were measured
with the prompt written in **Turkish**. The script shipped in this repository
asks for the same LRU cache in **English**, and on the identical engine that is
noticeably faster. The prompt language, not the engine, is the difference.

| | Turkish prompt (our published table) | English prompt (the shipped script) |
|---|---|---|
| Warm decode | 43–47 tok/s | **54–63 tok/s** |
| Acceptance | 44–48% | **57–69%** |

Compare your audit run against the **English** column.

Sources: Turkish column — memory-ladder rows 0.85 to 0.88 (the "C1 code" column
reads 43–47, and 46.2 at the production setting) and the post-reboot probe,
which recorded cold 44.1 / warm 43.0 / warm 44.0 with acceptance 43–44%.
English column — three consecutive runs on the production cluster, 4 Sep 2026,
raw data in [`results/speed/cold-warm-c1-english-prompt.txt`](../results/speed/cold-warm-c1-english-prompt.txt).

The gap is worth taking seriously: about 20% more speed and 13 points more
speculative acceptance, from nothing but the language of the request. The
DFlash2 drafter predicts English continuations much better than Turkish ones.
We have not rerun the whole speed battery in English, so every other speed
number in this repository is still the Turkish-prompt measurement — treat those
as a floor for English workloads rather than a ceiling.

A genuine cold first request (right after an engine start, before anything else
has touched the engine) is slower than either column: we measured 39–44 tok/s
with a TTFT of about 0.7 s. If you run the audit against an engine that has been
serving for a while, the "cold" line will not be cold.

Do not compare any of this against a synthetic "count to 200" number. On
synthetic prompts the same engine reaches 83–93 tok/s, because the drafter
accepts almost everything. That is the ceiling, not the working speed.

### category speed — `[measured-here]`

Four categories, six prompts each, concurrency 1 then concurrency 4.

| Category | C1 mean decode | C1 acceptance | C4 total |
|---|---|---|---|
| code | **45–48** tok/s | ~44% | **78–80** tok/s |
| json | ~52 tok/s | ~55% | **80–91** tok/s |
| math | ~57 tok/s | ~60% | ~80 tok/s |
| prose | **21–22** tok/s | **~13%** | ~38 tok/s |

Source: the category-speed run of 3 Sep 2026, and the C1-code / C4-json columns
of the memory ladder (46.2 and 80.1 at 0.88; C4 json normally reads 89–91, the
80.1 is a single reading).

The prose row is the honest headline of this whole stack: the DFlash2 drafter
barely fires on free prose, so speed falls back to roughly the unspeculated rate
(about 20 tok/s). Code, math and JSON get 2–2.8×. We did not measure *why* —
the drafter's training distribution and the intrinsic uncertainty of creative
text are both plausible and neither is tested.

One caveat on reproducing this exactly: our published category numbers were
measured with a prompt set in which half of each category was Turkish and half
English. The prompts shipped in `scripts/category-speed.py` are the English
translations of that set. Given what we measured on the cold/warm probe — the
same task in English runs about 20% faster with 13 points more acceptance — the
shipped script will very likely read **higher** than the table above, most of
all in the code and prose rows. We have not rerun the category battery in
English, so the table stands as measured and unmodified; treat it as a floor.

### free memory — `[measured-here]`

At `--gpu-memory-utilization 0.88`, with the engine idle:

| Node | Available memory | Swap |
|---|---|---|
| head | **4.6–4.7 GB** | **~450 MB** (439–456 MB measured) |
| worker-1 | **6.8–6.9 GB** | ~0 |
| worker-2 | **6.8–6.9 GB** | ~0 |

Source: memory ladder, 0.88 row, and the post-reboot production verification
(4.7 / 6.8 / 6.8 GB, head swap 456 MB).

On a GB10 the GPU shares host memory, so this figure *is* your safety margin.
Our rule: `MemAvailable` must stay at or above 2 GiB. If yours is lower than
ours, or swap is in the gigabytes, step `--gpu-memory-utilization` back down one
rung with `scripts/memory-ladder-step.sh` and re-audit. The head always has less
headroom than the workers — it carries the API server and the scheduler.

## If the audit disagrees with this page

Work in this order, because each step rules out the ones after it:

1. **Correctness first.** A fast engine that answers wrong is worthless. If the
   probe or the code exam fails, compare your image against `patches/` — every
   failure we have seen here traced back to a missing head-padding patch, not to
   a setting.
2. **Then the KV pool**, from the start-up log. It tells you whether the engine
   built the shape you asked for, before any timing is involved.
3. **Then memory**, because a machine that is swapping will produce speed
   numbers that mean nothing.
4. **Then speed**, and only against the matching row above, with the matching
   concurrency and the matching prompt category.

Anything you publish from this audit must carry the settings table with it. A
tok/s figure without its configuration is not a measurement.
