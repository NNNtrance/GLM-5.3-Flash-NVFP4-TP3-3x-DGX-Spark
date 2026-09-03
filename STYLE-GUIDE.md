# Style guide for authors (agents and humans) — read before writing anything

Audience: people (and their AI coding agents, e.g. Claude Code) who own 3× NVIDIA DGX Spark (GB10)
and want to run zai-org/GLM-5.3-Flash with this exact stack. Language: **English**. Tone: factual,
no marketing. Every number carries its settings. Every claim carries its evidence tier.

## Naming (privacy rule — NO exceptions)
- Machines: `head` (rank 0, serves the API), `worker-1`, `worker-2`. Never the real hostnames.
- IPs: use documentation addresses `192.0.2.10` (head), `192.0.2.11` (worker-1), `192.0.2.12` (worker-2),
  workstation/client `192.0.2.100`. Never real LAN addresses.
- Users/paths: `$USER`, `~/glm3x/` is fine (it is the install dir), never a real username or /home/<name>.
- Never mention: Tailscale, remote collaborators, our vault ("HAREM-vault"), internal file paths under /mnt/depo, e-mail addresses.
- The word **HAREM** appears inside the stack (image tag `harem/glm53-lil:t10`, service `harem-motor`,
  container `harem_glm53_lil`, patch markers `HAREM-*`, function names `_harem_*`, log lines). Keep them.
  README explains once: "HAREM is simply the name we gave our 3-node setup; it is hardcoded in several
  places — renaming requires care." Do not explain it again elsewhere.

## Evidence tiers (put one on every measured claim)
- `[measured-here]` we measured it on this cluster and the raw data is in `results/`
- `[measured-here, raw lost]` we measured it but the raw file did not survive — say so
- `[reported]` someone else reported it (link)
- `[estimate]` our estimate, not measured
- `[not tested]` we did not test it

## Every number needs its settings
engine build (image tag), TP/EP, quantization, KV dtype, speculative on/off (k), CUDA graphs on/off,
gpu-memory-utilization, temperature, thinking/effort, max_tokens, concurrency, prompt type
(synthetic / realistic), date. Put it in the table caption or a settings block above the table.

## Speed: synthetic vs realistic — always separated, always labelled
- Synthetic = "count 1→200", `clamp_00..49`, hash-map prose: shows the speculative-decoding CEILING. Label it.
- Realistic = 12 short English code prompts (hizset-v2), category prompts (prose/code/math/json).
- State plainly: synthetic numbers will disappoint in real use; prose acceptance is ~13%, code ~45–50%.

## Quality/benchmarks — always say: all run at reasoning effort **low**, temperature 0 unless stated.
Explain why (max effort would take days on this cluster; measured low→max token ratio 4.6–18×).
Give an estimate of what max effort would change, marked `[estimate]`.

## Honesty sections (mandatory)
- "What we tried and rejected" with reason + evidence tier.
- "Open problems" (unsolved) and "Retracted" (numbers we withdrew and why).
- "What this costs" line for every gain: speed / quality / memory together.

## Credits & licenses
Every external component: name, link, exact revision (commit / HF sha / image digest), license, what we use it for.
Our own patches: "written by us for this recipe; use freely (Apache-2.0); a credit is appreciated" — and say when
a patch was adapted from someone else's idea (name + link). DFlash2 draft: cc-by-nc-nd-4.0 + a project-specific,
non-transferable permission we obtained — readers must obtain their own; we do not redistribute the draft.

## Formatting
Markdown, tables for numbers, fenced code blocks for commands (one command per block, no `$` prompt),
relative links between docs, no emojis, no exclamation marks. Commands must be copy-paste runnable on the nodes.
