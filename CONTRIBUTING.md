# Contributing — help us close the gaps

We could not run everything. If you own a comparable cluster and run one of the items below, please open a
pull request that adds your raw output under `results/community/<your-handle>/<item>/` plus a short Markdown
summary. Every number needs its settings (image tag, TP/EP, quantization, gpu-memory-utilization, temperature,
reasoning effort, max_tokens, concurrency, prompt type, date) and an evidence tier as defined in
[STYLE-GUIDE.md](STYLE-GUIDE.md). We will credit you in [CREDITS.md](CREDITS.md).

## Items we did not run (most useful first)

1. **ExtractBench Short on this build.** Runner: `run-llama/ExtractBench` at commit `e86af28c` (Apache-2.0).
   Rules we follow for a fair comparison are in [docs/06-benchmarks.md](docs/06-benchmarks.md#not-run): Short set only
   (the encoder budget of ~32K tokens and ~2,837 tokens per page make Medium/Long impossible here), the 215-document
   intersection used for the earlier baseline, concurrency 4 (drop to 2 if any node falls under 2 GiB free).
   Report the score, the number of documents, and the free-memory snapshot before and after.
2. **IFEval cross-check with the official `lm-eval` task.** Our 78.9 % came from tool-eval-bench's own evaluator and we
   flagged one doubtful case. Run:
   ```bash
   scripts/run-lm-eval.sh ifeval-crosscheck 0 ifeval
   ```
   and report prompt-level strict/loose and instruction-level strict/loose. About one hour.
3. **Max-effort reruns.** All our benchmark numbers are at reasoning effort `low`. Rerunning tool-eval-bench (2 trials),
   GSM8K and IFEval with `reasoning_effort=max` (pass `chat_template_kwargs`) would replace our `[estimate]` rows in
   [docs/06-benchmarks.md](docs/06-benchmarks.md#all-benchmarks-were-run-at-reasoning-effort-low). Expect 5–12× the time.
4. **Two-node (TP=2) variant** of this recipe with the same image, for readers with two Sparks: KV pool, C1–C8 speeds,
   and whether the b12x head-padding is still needed at that shape.
5. **The newer DFlash2 draft revision (`bf582e4e`)**: a clean A/B against `dc77ff1c` with the code exam and the
   correctness probe, to settle the open item in [docs/08-what-we-tried.md](docs/08-what-we-tried.md).
6. **k=7 vs k=8 speculative depth** in one clean session, two rounds each (see the open item in docs/03 and docs/08).

## How to submit

- Fork, add your files, open a pull request. Keep our documents unchanged unless you are correcting an error;
  in that case cite the raw file that shows the correction.
- Never include hostnames, LAN addresses, user names or tokens in what you upload (grep before you push).
- If you found a mistake in our numbers, say so plainly; we keep a "Retracted" section in
  [docs/09-open-problems.md](docs/09-open-problems.md) and will add it there with credit.
