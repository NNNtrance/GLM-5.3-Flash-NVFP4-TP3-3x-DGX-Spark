# 04 — Autostart: the engine comes back without you

Three machines, one engine. Every one of them has to be running for any of them
to be useful, and that is exactly the situation in which "just start it by hand"
stops being an answer.

## Why autostart at all

The engine is not a service you restart casually. It takes about five minutes to
go from cold boot to serving, it needs a fabric that is not ready when the OS
is, and it must be started in a specific order across three machines. If that
work is manual, then every power cut, every UPS test, every unattended kernel
update ends with an installation that is silently down until a human notices and
walks through the sequence correctly.

So each node runs the same systemd unit, `harem-motor.service`, enabled at boot.
After a power loss the cluster brings itself back with nobody logged in.

The unit is deliberately dull: it waits for the preconditions, launches one rank,
and stops the container cleanly. All the interesting knowledge lives in the
preflight and in the env file.

## The chain

```
power on
  └─ systemd: multi-user.target
       └─ docker.service (Requires=, After=)
            └─ harem-motor.service        [on all three nodes, independently]
                 ├─ ExecStartPre  scripts/engine-preflight.sh
                 │     1. docker daemon answering
                 │     2. ConnectX-7 4/4 PORT_ACTIVE
                 │     3. both fabric neighbours reachable
                 │     4. sync + drop_caches
                 ├─ ExecStart     scripts/start-lil.sh
                 │     reads ENV_FILE, takes NODE_RANK from it,
                 │     docker run -d  (one rank)
                 └─ ExecStop      docker stop -t 60 harem_glm53_lil
```

Each node runs this on its own. There is no orchestrator, no leader election and
no shared state: the three ranks find each other through the NCCL rendezvous at
`MASTER_ADDR:MASTER_PORT`, and whichever rank arrives first simply waits.

### Why the preflight exists

At boot, systemd will happily start the engine before the network fabric is
usable. The failure that produces is unpleasant to debug: the container starts,
NCCL blocks in the rendezvous, nothing errors, and the unit sits in `activating`
until `TimeoutStartSec` fires fifteen minutes later. Nothing in that sequence
names the missing cable.

So the preflight refuses to hand over until the node can actually participate:

1. **docker up.** `docker info` answering, not merely `docker.service` being
   "started". Cap: 5 minutes.
2. **ConnectX-7 4/4.** `ibv_devinfo | grep -c PORT_ACTIVE` must equal `4`. Fewer
   than four means at least one leg of the triangle is missing. Cap: 10 minutes
   from the start of the preflight.
3. **Fabric peers reachable.** A `ping` to each of this node's two fabric
   neighbours. Ports report `PORT_ACTIVE` before the addressing is up, so the
   port count alone is not sufficient.
4. **`sync` + `drop_caches`.** The weight load reads ~62 GiB from disk. Starting
   from a clean page cache makes load time repeatable, which matters when you are
   comparing configurations.

If any check times out the unit fails loudly, with a message naming the specific
thing that is missing.

### Why the env file carries the rank

`start-lil.sh` normally takes the rank as `$1`. Under systemd there is no
argument, so it sources `ENV_FILE` and reads `NODE_RANK` from there. That is why
**each node needs its own env file** and why copying one between nodes is
forbidden: the copy carries the head's `NODE_RANK=0` and the head's `HOST_IP`,
two ranks then announce themselves as rank 0, and the rendezvous hangs — again
with no error that names the cause. Derive each node's copy with `sed`; the
recipe is in the header of `scripts/env.example`.

## Reboot all three, always

**A single-node reboot takes the peer's fabric port down with it.**

The nodes are wired as a pairwise QSFP triangle with no switch. When one node
goes down, the link partner on the other end of each of its two cables loses
that port. So rebooting one node does not leave "two healthy nodes and one
rebooting node" — it leaves two nodes that have each lost a leg of the triangle,
running an engine job whose third rank has vanished.

We learned this the hard way while chasing a related fault: a hotplug helper
shipped with the platform made single-node recovery worse, and after removing it
(`/etc/nvidia/cx7-hotplug-enabled` deleted on all three nodes) the **three-node
simultaneous reboot works reliably**, verified 2 Sep 2026.

The rule:

- Reboot **all three together**.
- After the reboot, before expecting the engine, confirm `ibv_devinfo` reports
  4/4 on every node — the preflight does this for you, but check it yourself if
  you are debugging.
- Do not restart one node "just to try something". Stop everything, change what
  you need on all three, start everything.

`scripts/engine-stop.sh` and `scripts/engine-start.sh` do the fleet-wide stop and
start in the right order (workers first, head last).

## Verification

### 1. Boot to serving

Measured on this cluster: **296–304 s** `[measured-here]`.

Three-node reboot, 3 Sep 2026, timed from the reboot command:

| Milestone | Elapsed |
|---|---|
| ssh answering again | +101 s |
| ConnectX-7 4/4 | +103 s |
| `harem-motor` started | +104 s |
| engine answering `/health` | **+304 s** |

A second production reboot the same night: **296 s**. During the memory-ladder
work, manual restarts of an already-warm machine came up in 210–225 s. If your
cluster takes materially longer than five minutes, the preflight log will tell
you which stage is waiting.

### 2. Watch it come up

```bash
journalctl -u harem-motor -f
```

```bash
docker logs -f harem_glm53_lil
```

### 3. Log lines to look for

The preflight, once it clears — this is the first thing to appear, and its
absence means you are stuck on the fabric, not on the engine:

```
preflight ok: 47 s, ConnectX-7 4/4, peers: ...
```

The launcher's own summary, confirming the shape it is about to run:

```
IMAGE=harem/glm53-lil:t10 rank=0 nnodes=3 tp=3 ep=1 moe=marlin k=7 transport=mesh
```

Expert parallelism actually taking effect — 96 of 288 experts on this rank, the
proof that EP is on and the experts were not sharded the wrong way:

```
expert_map_manager ... 96/288
```

The KV pool the engine built. At `--gpu-memory-utilization 0.88` expect about
4.32M tokens `[measured-here]`:

```
GPU KV cache size: 4,321,739 tokens
```

Serving, at last:

```
Application startup complete
```

### 4. Prove it is serving, not merely running

```bash
curl -s http://192.0.2.10:8000/health && echo UP
```

Then run the real check — health only means the process is alive, and an engine
with a broken decode path answers `/health` perfectly:

```bash
audit/run-audit.sh health kv probe
```

Expect 10/10 on the correctness probe. See [audit/README.md](../audit/README.md).

## Turning it off

For a maintenance window, stop everywhere but keep autostart:

```bash
scripts/engine-stop.sh
```

To also stop it coming back at the next boot:

```bash
scripts/engine-stop.sh disable
```

`engine-start.sh` re-enables and starts, in the correct order.
