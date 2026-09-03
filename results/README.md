# results — raw measurement artefacts

Every number in [`../docs/06-benchmarks.md`](../docs/06-benchmarks.md) and
[`../docs/07-speed.md`](../docs/07-speed.md) traces to a file in here, or is explicitly marked
`[measured-here, raw lost]`, `[reported]`, `[estimate]` or `[not tested]` in those pages.

All of it was produced on 2026-09-03 with the `harem/glm53-lil:t10` image, TP = 3 + expert parallelism,
NVFP4 weights, `fp8` KV, DFlash2 draft k = 7, CUDA graphs on, temperature 0, thinking on at
`reasoning_effort: low`. The exact settings per file are in the two documents above.

## Scrubbing

These files are copies of our own run output. Before committing, host names were replaced with
`head` / `worker-1` / `worker-2`, the workstation name with `workstation`, our LAN address with the
documentation address `192.0.2.10`, and local absolute paths with placeholders. The benchmark harness had
already masked the server address as `***` inside the tool-eval-bench JSON. Numbers, timings and model output
are untouched. Turkish labels in our own runner logs were translated to English; the values are unchanged.

E-mail addresses and IP addresses that appear inside benchmark *content* (IFEval prompts, tool-eval-bench
scenario fixtures, MMLU questions) are part of those public datasets, not ours.

## What is here

| Path | What it is |
|---|---|
| `tool-eval-bench/tool-eval-bench-8-trials.json` | Full aggregate result: 8 trials × 88 scenarios, per-scenario point vectors, per-category means, safety warnings, run metadata |
| `tool-eval-bench/trial-1-report.md` … `trial-8-report.md` | The eight individual trial reports, in run order |
| `tool-eval-bench/three-model-comparison.html` | Our side-by-side page: this run next to the two models from MiaAI-Lab's published comparison. Read the version caveat in the docs before comparing |
| `ifeval/ifeval-541-prompts-report.md` | Per-constraint accuracy and all 114 failing prompts |
| `gsm8k/gsm8k-200-questions-8shot-report.md` | Accuracy plus all 12 failures with the model's full working |
| `needle/needle-in-a-haystack-report.md` | The 4 sizes × 5 depths retrieval grid, to 997,952 tokens |
| `mmlu/mmlu-full-lm-eval-results.json` | `lm-eval` 0.4.9 output for the full 14,042-question run (per-subject accuracy, stderr, task configs) |
| `mmlu/mmlu-per-subject-accuracy.md` | The same accuracies as a readable 57-row table |
| `memory-ladder/memory-ladder-0.85-to-0.89.log` | The `gpu-memory-utilization` climb: KV pool, free RAM per node, swap, correctness probe, cold/warm and category speed at each rung |
| `memory-ladder/reboot-verification-0.88.log` | Reboot to serving endpoint at the chosen production value: 296 s, KV 4,321,739 tokens, probe 10/10 |
| `speed/bench-sweep-*.json` | Concurrency sweeps (C1, C2, C4, C6, C8), two rounds each. `reboot-t10` is the post-reboot production run; `lil-t10` the same settings earlier the same day; `lil-t16` graphs off; `lil-t17` graphs captured only at sizes 8 and 16; `lil-t9` speculative decoding disabled; `lil-t7` the image before the attention head-padding fix |
| `speed/category-speed-c1-c4.log` | Decode rate and draft acceptance by content type (prose / code / math / JSON) at C1 and C4 |
| `speed/cold-warm-c1-english-prompt.txt` | The cold/warm single-stream probe re-run with the task written in English, showing that prompt language alone is worth ~10–15 tok/s and ~13 points of draft acceptance |

## Reading the sweep JSONs

Each file is a list of five objects, one per concurrency level:

| Field | Meaning |
|---|---|
| `conc` | concurrent streams |
| `requests` / `out_tok` | requests issued and total output tokens (256 tokens per request) |
| `per_stream_decode_tok_s` | decode rate one user feels — `(tokens − 1) / (end − first token)` |
| `agg_tok_s` | total output tokens over total wall clock, including prefill and inter-request gaps. At C1 this is lower than `per_stream_decode_tok_s`; that is expected, not an error |
| `ttft_med_s` / `ttft_max_s` | time to first token |
| `tpot_ms` | mean time per output token |
| `accept_rate_pct` / `accept_len` | speculative-decoding acceptance from the engine's `/metrics`, and mean tokens produced per model step |

`accept_rate_pct` is `null` in the `lil-t9` files because that arm ran with the draft model disabled.
