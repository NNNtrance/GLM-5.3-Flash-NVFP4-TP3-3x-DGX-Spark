# 05 — The memory ladder

`--gpu-memory-utilization` is the single highest-value knob left once the engine works.
Every percentage point buys real KV cache. This page is the measured climb from 0.85 to
0.89 on our cluster, the reason we stopped at 0.88, and the protocol to repeat it on yours.

**0.85 is a starting value, not a limit.** Bring a new setup up at 0.85, prove it is
correct and fast, then climb one step at a time with a measurement at each step.

---

## 1. How memory works on GB10

GB10 has **unified memory**: there is no separate GPU pool to fill. Everything the engine
allocates comes out of the same 128 GB the operating system is living in. That is the whole
reason this knob is delicate — pushing the engine up pushes the host down.

Per node, at production settings `[measured-here]`:

| | |
|---|---|
| Total memory visible to the OS | 121.6 GiB |
| Free on the device as the engine sees it at startup | **111.5–111.8 GiB** |
| Model weights | ~65.5 GiB (engine reports 65.78 GiB at load) |
| Peak activations | 5.8 GiB at 0.86, rising to 6.3 GiB at 0.89 |
| Everything left over | the KV pool |

The difference between 121.6 and 111.7 GiB is held by the driver and does not come back
after a reboot. So the **device-side ceiling is about 0.915** — ask for more than that and
vLLM refuses during its pre-check, cleanly, without allocating anything. (An earlier
measurement in our pinned-KV era saw only 107.4 GiB free, a ~14 GiB driver reserve, and put
the ceiling at 0.883. The current stack sees more.)

**The device ceiling is not the binding limit.** Long before 0.915, the host runs out of
free memory and starts swapping. The real limit is *host free memory*, and the rule we
work by is:

> **MemAvailable must stay above 2 GiB on every node, and swap must stay at zero.**

The engine, the container runtime, systemd, the page cache and your SSH session all live
in the memory you take away from the host. When that gets tight, the kernel does not throw
an error — it quietly pages the engine's own memory out to swap and reports the freed
space as "free". That number is a lie, and section 4 is what it looks like.

## 2. Settings for every number on this page

```
image harem/glm53-lil:t10 · TP=3 + expert parallelism · NVFP4 mixed (marlin) ·
--kv-cache-dtype fp8 · --block-size 256 · --max-model-len 1000000 · --max-num-seqs 8 ·
speculative decoding DFlash2 k=7 · CUDA graphs on, AOT compile on · no KV pin
(--kv-cache-memory not set) · temperature 0 · thinking on, reasoning effort low ·
max_tokens 700 · realistic category prompts · measured 3 September 2026
```

Speed columns are decode throughput in tokens/s: **C1 code** is one stream on a code
prompt, **C4 json** is four concurrent streams on JSON prompts. Quality is our short
correctness probe (10 prompts) and the code gate (12 prompts).

## 3. The measured ladder

`[measured-here]` — one engine restart per step, same prompts, same order, same night.

| `gpu-memory-utilization` | KV pool (tokens) | vs base | Concurrency at 1M ctx | `head` free | `worker-1` / `worker-2` free | `head` swap | Speed C1 code / C4 json | Quality |
|---|---|---|---|---|---|---|---|---|
| 0.85 (base) | 3,881,159 | — | 3.88× | 6.4–7.6 GB | 9.2 / 9.1 GB | 0 | 43–47 / 90 | 10/10, 12/12 |
| 0.86 | 4,023,188 | +3.7 % | 4.02× | 7.0 GB | 9.3 / 9.2 GB | 41 MB | 47.5 / 90.5 | 10/10 |
| 0.87 | 4,156,521 | +7.1 % | 4.16× | 5.8 GB | 8.2 / 8.1 GB | 47 MB | 45.6 / 91.0 | 10/10 |
| **0.88 — production** | **4,310,144** | **+11.1 %** | **4.31×** | **4.6–4.7 GB** | **6.8–6.9 GB** | **439–456 MB** | 46.2 / 80.1 \* | 10/10 |
| 0.89 — rejected | 4,408,695 | +13.6 % | 4.41× | 5.2 GB † | 5.8–6.0 GB | **927 MB** | 45.5 / 89.5 | 10/10 |

\* single reading, probably noise — C4 json measures 89–91 everywhere else, including on
the same setting after the production reboot.
† inflated. See section 4.

Notes on the table:

- Workers never swapped at any step. The head node is where the pressure lands, because
  rank 0 also runs the API server and the rendezvous.
- Free memory falls roughly **1.2 GB per node per step**, linearly.
- The engine came up in 210–225 s at every step. No step was slower to start.
- **Speed and quality did not change at any step.** Correctness stayed 10/10 throughout,
  and decode throughput moved inside its normal spread. A bigger KV pool does not make the
  model faster; it makes it able to hold more concurrent or longer conversations.
- After choosing 0.88 we rebooted all three nodes and re-measured cold: pool
  **4,321,739** tokens, correctness 10/10, free memory 4.7 / 6.8 / 6.8 GB, head swap
  456 MB. The pool differs slightly from the pre-reboot number because the profiler
  re-measures free memory each start.

Arithmetic that lets you predict your own numbers: at `fp8` KV dtype this model costs
**≈8.6 KB per token** of KV cache (35.98 GiB holds 4.37M tokens).

## 4. The decision: 0.88

**Production runs at 0.88**, for +11.1 % KV pool over the 0.85 baseline.

**0.89 was rejected.** Not because anything failed — correctness was 10/10 and speed was
unchanged — but because of what the memory numbers were doing underneath:

- Head swap **doubled**, 439 MB to 927 MB.
- Reported free memory went *up*, from 4.6 to 5.2 GB, while the container's resident set
  went *down*, 10.2 GiB to 8.8 GiB. The kernel was paging the engine's own pages out to
  disk and reporting the result as free memory.

That is the failure mode we care about. It costs nothing on a short probe and it costs a
great deal on a long context under real concurrency, when those paged-out weights have to
come back from NVMe. A configuration that looks fine on a five-minute test and is quietly
swapping the engine is worse than one percent less KV cache.

At 0.88 the 439 MB of swap appeared at startup and **did not grow under load**, and free
memory stayed above the 2 GiB rule on all three nodes. That is the last step we were
willing to call safe. It is a yellow flag, not a green one: it is the first setting at
which swap was allocated at all.

**0.90 and 0.91 would probably reach around 5M tokens `[estimate]`, and we did not
consider them safe enough to test.** Extrapolating the linear fall in free memory puts the
head node at roughly 2.2 GB free at 0.90 — exactly at the rule's boundary, with swap
already growing at 0.89. We stopped.

**What this costs.** +11.1 % KV pool costs 1.8–2.9 GB of free memory per node, 439–456 MB
of swap on the head node, and the diagnostic headroom that free memory represents when
something goes wrong at 02:00. Speed: unchanged. Quality: unchanged.

## 5. Climbing the ladder on your own machine

Do not copy our 0.88. Your driver reserve, kernel, container runtime and background
services are not ours, and the binding limit is host memory, not the device.

Start at **0.85** and take **one step at a time**. Two scripts do the work:
[`scripts/memory-ladder-step.sh`](../scripts/memory-ladder-step.sh) sets the fraction and
restarts the engine on all three nodes;
[`scripts/memory-ladder-measure.sh`](../scripts/memory-ladder-measure.sh) collects the
readings below into `results/memory-ladder/`.

At **every** step, in this order:

1. **Boot the engine** and wait for it to serve. Note how long it took; a step that
   suddenly starts much slower is a signal.
2. **Read the KV pool line** from the engine log — the number of tokens it allocated. This
   is the gain you are buying.
3. **Read free memory on every node**, not just the head. `MemAvailable` is the number
   that matters.
4. **Read swap on every node.** Zero is the expected value. Any non-zero figure is a flag,
   and a figure that *grows under load* is a stop.
5. **Run a correctness probe.** A short fixed set of prompts with a known-good answer, at
   temperature 0. Ours is 10 prompts plus a 12-prompt code gate.
6. **Run a short speed check** at one stream and at four. You are not looking for a
   speedup — there will not be one — you are looking for a regression.

**Stop climbing when any node drops below 2 GiB free, or when swap appears and grows.**
Then step back down one notch and make that your production setting.

Two practical notes. Ask for too much and vLLM refuses during its pre-check and exits in
under a minute without allocating anything, so an over-ambitious step is a safe failure,
not a crash. And do not change kernel or sysctl settings in the same session — one
variable at a time, and system settings get tried on a loose test branch first
(see [00-prerequisites.md](00-prerequisites.md), section 5).

## 6. KV pool versus context: what the number actually means

The most common misunderstanding: "the pool is 4.3M tokens, so I will divide it among my
agents." That is not how it works.

**The pool is shared, and it is consumed only by requests that are being processed right
now.** An agent that is not talking occupies zero. The "context length" in an agent's
profile is not a reserved budget; it is a ceiling on how long that one conversation may
grow. You can define fifty agent profiles against this engine.

**The real limit is concurrency, not the pool.** With `--max-num-seqs 8`, at most 8
requests are in flight at any moment, whatever the pool size. Worst case is 8 × the
per-request context: at a 4.31M-token pool that is roughly 539K tokens each before the
pool is the constraint. Our `--max-model-len` is 1,000,000 tokens, and the pool holds
**4.3 full-length requests** at that length. Note the two conventions: the table above
divides by 1,000,000 and says 4.31×, while vLLM prints its own ratio against 1,048,576 and
says 4.11×. Same pool, two divisors.

Verified end to end: a needle-in-a-haystack run scored 20/20 across four context sizes up
to 974K tokens and five depths, with an effective context of 997,952 tokens
`[measured-here]`.

**Running out of pool does not crash anything.** Requests wait — vLLM exposes
`vllm:num_requests_waiting_by_reason{reason="capacity"}` — and a running request can be
preempted and resumed. A pool that is too small costs you latency, not stability.

**Finished requests do not free their blocks immediately.** They stay in the prefix cache
as reusable, and are evicted oldest-first when space is needed. An idle session's KV
blocks the engine for nobody but is still there if that session comes back.

One more thing worth knowing when you size this: **vLLM has no memory of its own.** The
conversation lives in your client, and the whole history is re-sent on every request. The
KV cache is not memory; it is a "I already computed this prefix" shortcut. Which is why
prefix caching only helps if the **beginning** of the conversation stays byte-identical —
edit a system prompt or rewrite history in the middle and everything after that point is
recomputed. Prefill runs at ~1,560 tok/s and decode at ~46 tok/s on this stack, so that
recomputation is cheap per token and expensive in total.

---

Previous: [01-cluster-setup.md](01-cluster-setup.md).
