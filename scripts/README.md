# scripts

Everything needed to launch the engine and to measure it. Two groups: scripts
that run **on a node** (the launcher and its preflight) and scripts that run
**on your workstation** and reach the cluster over ssh and HTTP.

Every script here was written by us for this recipe unless its header says
otherwise. Use freely (Apache-2.0); a credit is appreciated.

## Configure once

```bash
cp cluster.env.example cluster.env
```

Edit `cluster.env` with your ssh targets and API address. It is read by every
shell script in this directory (via `lib-cluster.sh`) and it is gitignored. The
defaults are RFC 5737 documentation addresses, so nothing works until you fill
it in.

The engine's own configuration is separate: copy `env.example` to
`~/glm3x/.env.lil-t10` **on each node** and derive the three per-node lines with
`sed`. Never copy one node's env file to another — the header of `env.example`
explains what breaks and how to derive it safely.

The evaluation runners need a couple of paths for third-party tools:
`LM_EVAL`, `TOOL_EVAL_BENCH`, `TOKENIZER`. Set them in `cluster.env` too.

**What the ssh account on each node needs.** The operator-side scripts run non-interactively
(`ssh -o BatchMode=yes`, `sudo -n`), so the account named in `cluster.env` must have:

- key-based ssh with no passphrase prompt;
- membership of the `docker` group, because `run-audit.sh` and the memory scripts read
  `docker logs` and container memory;
- passwordless sudo for `systemctl start|stop|enable|disable harem-motor` — used by
  `engine-start.sh`, `engine-stop.sh` and `memory-ladder-step.sh`. Without it those three
  scripts fail silently on every node.

The service account that the systemd unit itself runs as is a separate matter, including
its own `drop_caches` sudoers line — see [systemd/README.md](../systemd/README.md).

## On a node

| File | What it does |
|---|---|
| `start-lil.sh` | Launches one rank. Takes the rank as `$1`, or reads `NODE_RANK` from `ENV_FILE` (which is how systemd calls it). `DRY_RUN=1` prints the docker command and runs nothing. |
| `engine-preflight.sh` | `ExecStartPre` for the systemd unit: waits for docker, ConnectX-7 4/4, both fabric neighbours, then drops caches. Set `FABRIC_PEERS` per node. |
| `env.example` | The production engine configuration (`t10`), one comment per variable. |

## From the workstation

| File | What it does |
|---|---|
| `engine-start.sh` | Starts the engine on all three nodes, workers first, and re-enables start-at-boot. |
| `engine-stop.sh` | Stops it everywhere. `engine-stop.sh disable` also turns off start-at-boot. |
| `free-memory-snapshot.sh` | One line of available/used/total memory and container RSS across the three nodes. |
| `memory-ladder-step.sh` | Moves `--gpu-memory-utilization` one rung, restarts the fleet, waits for health, hands over to the measurement half. Carries the measured ladder table in its header. |
| `memory-ladder-measure.sh` | The measuring half: idle memory, correctness, cold/warm C1, category speed, memory under load, KV pool. |

## Measurement

| File | What it measures |
|---|---|
| `correctness-probe.py` | 10 checkable answers + one repetition-lock check. The cheapest gate; run it after every start. Exit 0 on a full pass. |
| `code-exam.py` | 12 programming tasks, executed and asserted. Runs model-written python locally with no sandbox. |
| `cold-warm-c1.py` | Cold vs warm single-stream decode and speculative acceptance on one code prompt. |
| `category-speed.py` | Decode speed and acceptance per workload: prose / code / math / json, at concurrency 1 and 4. The script that shows how differently the drafter behaves per category. |
| `bench-sweep.py` | Concurrency sweep C1..C8: aggregate and per-stream tok/s, TTFT, speculative counters. The source of every speed table we publish. Uses `hizset-v2.jsonl`. |
| `community-protocol.py` | The synthetic protocol other DGX Spark owners publish (count-to-200, clamp functions, hash-map prose). Shows the speculative ceiling — label it as synthetic. |
| `hizset-v2.jsonl` | The 12 short English code prompts `bench-sweep.py` cycles through. |

Two warnings that apply to any number these produce:

- **Synthetic and realistic are different measurements.** `community-protocol.py`
  reaches 83–93 tok/s on its structured and code prompts; the same engine does
  43–47 tok/s on a real code request and 21–22 tok/s on prose. Always say which
  one you ran.
- **`bench-sweep.py` cycles a fixed prompt list**, so from the second lap onward
  every request is a prefix-cache hit. Relative comparisons between two arms
  survive that; absolute TTFT and prefill numbers from this script do not.

## Benchmark runners

These drive third-party harnesses that are **not** part of this recipe and are
not redistributed here. Install them yourself, check their licences, and point
the env vars at them.

| File | Harness | Roughly |
|---|---|---|
| `run-lm-eval.sh` | lm-evaluation-harness | MMLU sample ~20 min, full MMLU ~2 h |
| `run-tool-eval-bench.sh` | tool-eval-bench | 88 scenarios × 8 trials, ~90 min |
| `run-ifeval.sh` | tool-eval-bench | 541 prompts, ~2 h 10 min |
| `run-gsm8k-needle.sh` | tool-eval-bench | GSM8K ~8 min, then needle |
| `run-needle.sh` | tool-eval-bench | needle alone with `--timeout 3600`, ~88 min |

Two timeouts are load-bearing and are documented in the scripts themselves: the
lm-eval `timeout=36000` (its aiohttp `total` timeout counts queue waiting time,
so a full MMLU run dies without it) and the needle `--timeout 3600` (a 250k-token
prefill alone exceeds the 120-second default).

## Order to run things after an install

```bash
audit/run-audit.sh
```

That wraps the probe, the code exam, cold/warm C1, category speed and the memory
snapshot, and prints the expected range next to each. See
[audit/README.md](../audit/README.md) for what the numbers should be and what to
do when they are not.
