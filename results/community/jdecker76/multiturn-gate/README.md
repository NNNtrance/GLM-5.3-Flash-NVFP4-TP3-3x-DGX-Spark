# Multi-turn agentic gate — contributed tool, and the run that motivated it

Contributed by **@jdecker76**. Tool: [`scripts/multiturn-gate.py`](../../../../scripts/multiturn-gate.py).

## Why

Every gate in this repository is **single-turn** — the correctness probe, the code
exam, the tool-call gate. We ran a GLM-5.3-Flash stack in production on real
multi-user agentic coding traffic that passed all of them (10/10, 12/12, 8/8,
cold and warm) while users hit **looping, premature end-of-turn and degraded
long-session quality** badly enough that we rolled the engine back. The symptom
family lives in warm multi-turn sessions, so no single-turn gate could see it.

## What it checks

| | Check | Method |
|---|---|---|
| A | **Reasoning retention** | Runs the same 5-turn conversation twice — once echoing assistant turns **verbatim** (what real clients send), once with `<think>` stripped by the harness — and compares prompt-token growth. A ratio above the threshold means prior reasoning is being re-rendered into later prompts. |
| B | **Degenerate repetition** | Longest run of a repeated line, plus a repeated-phrase detector, per response. |
| C | **Early stop** | `finish_reason == "stop"` at an implausibly short length on prompts that explicitly ask for substantive output. |

Check A is deliberately **differential**. It measures the rendered result rather
than reading configuration, so it is engine- and template-agnostic: it reports
what the server actually did.

## The run that motivated it

Settings: GLM-5.3-Flash, TP=3 + EP on 3× DGX Spark (GB10), NVFP4 (`modelopt_mixed`),
fp8 KV, DFlash2 k=7, `--block-size 256`, `gpu-memory-utilization` 0.88,
`max-model-len` 1000000, `max-num-seqs` 8, temperature 1.0 / top_p 0.95,
`reasoning_effort` low, 5 turns × 2 arms, chat template `04c4e9e9` + local edits,
9 September 2026. `[measured-here]` — "here" being our cluster, not this repo's.

| Arm | prompt_tokens by turn | growth | ratio |
|---|---|---:|---:|
| Before: engine defaults carried `clear_thinking:true`, request sent its own `chat_template_kwargs` | 52 · 518 · 1166 · 1549 · 2275 | +2223 | **2.24** |
| After: template itself defaults `clear_thinking` to true | 52 · 309 · 704 · 937 · 1511 | +1459 | **1.13** |

**The finding worth carrying over:** engine-level `--default-chat-template-kwargs`
are **not** a guard. A per-request `chat_template_kwargs` block **replaces** the
defaults rather than merging, so any client sending `{"reasoning_effort":"low"}`
silently drops `clear_thinking` and retention returns. We believed we had fixed
this a day earlier by adding the key to the launcher defaults; this gate is what
showed us we had not. That is the argument for checking rendered behaviour rather
than configuration.

Both arms: no degenerate repetition, no early stops.

## Not tested

- Beyond 5 turns; retention shows up by turn 3 but a longer horizon is untested `[not tested]`.
- Tool-call turns inside the multi-turn arm — the conversation is plain text; a tool-calling
  variant would exercise the template's tool paths as well `[not tested]`.
- Thresholds (`--retention-ratio 1.35`, `--min-stop-tokens 120`, `--max-repeat 4`) are
  judgement calls from one cluster, not measured flake baselines. Anyone adopting this
  should establish their own baseline first — `9496fcc` in the EXL3 sibling is the
  cautionary tale for adjudicating changes against a gate whose flake rate is unknown.
