# 01 — Cluster setup

Three nodes, one ring of cables, one thing that will waste a day of your life if nobody
warns you about it (section 5). Read section 5 before your first reboot, not after.

Prerequisites: [00-prerequisites.md](00-prerequisites.md).

---

## 1. Node roles

| Role | What it does |
|---|---|
| `head` | rank 0. Runs the rendezvous master and **serves the OpenAI-compatible API on port 8000**. This is the only node a client talks to |
| `worker-1` | rank 1 |
| `worker-2` | rank 2 |

All three run the same image, the same service and the same env file; only the rank, the
node's own IP and the plugin path differ. The engine is tensor-parallel across all three
(TP=3) with expert parallelism enabled, so **all three must be up for the model to serve
anything**. There is no graceful degradation to two nodes.

One consequence worth planning around: **the slowest node sets the pace of the cluster.**
Our three units sustain 2502 / 2485 / 2440 MHz — a 2.5 % spread that is ordinary silicon
binning, not a fault `[measured-here]`. If one of your nodes is clock-limited or thermally
throttled, all three run at that speed.

## 2. Addressing

Two separate networks. Do not mix them up.

**Management network** — ordinary ethernet, one onboard interface per node (`enP7s7` on
our units; confirm yours with `ip -br link`). SSH, `docker`, and the API listener live
here. This repository uses documentation addresses throughout:

| Node | Management address |
|---|---|
| `head` | `192.0.2.10` |
| `worker-1` | `192.0.2.11` |
| `worker-2` | `192.0.2.12` |
| workstation / client | `192.0.2.100` |

Replace them with your own. The operator-side scripts read them from
[`scripts/cluster.env`](../scripts/cluster.env.example).

**Fabric network** — the ConnectX-7 ring, used only by NCCL. Six point-to-point links,
each on its **own /24**. This is an example plan, not a requirement; use whatever
addresses do not collide with your house network. Check this before you start: NVIDIA's
own playbook example puts the fabric on a range that many home routers already use, and
we had to move ours to avoid exactly that collision.

| Link | Endpoints |
|---|---|
| `head` ↔ `worker-1`, cable 1 | `10.10.0.1/.2` and `10.10.1.1/.2` |
| `head` ↔ `worker-2`, cable 3 | `10.10.2.1/.2` and `10.10.3.1/.2` |
| `worker-1` ↔ `worker-2`, cable 2 | `10.10.4.1/.2` and `10.10.5.1/.2` |

Each node exposes four fabric interfaces (`enp1s0f0np0`, `enp1s0f1np1`, `enP2p1s0f0np0`,
`enP2p1s0f1np1`) and four RoCE devices. We configure them as persistent NetworkManager
profiles with **MTU 9000** and `ipv4.never-default yes`, so the house network stays the
default route.

Note for later: the ConnectX-7 card does **not appear in `lspci` while its cable is
unplugged**. If a card is missing, check the cable before you suspect the hardware.

## 3. The NCCL mesh plugin

A three-node direct-attached triangle is not a normal NCCL topology. Each pair sits on its
own subnet, so NCCL cannot pick a NIC by matching subnets the way it does on a switched
fabric, and the usual advice for two-node setups makes things worse here.

The **mesh plugin** is a NCCL net plugin that selects the local NIC based on the peer's
address, so each rank uses the cable that actually reaches that peer.

| | |
|---|---|
| Source | `https://github.com/autoscriptlabs/nccl-mesh-plugin` |
| Commit we cloned | `19924dcc7c571d6e260953724d394ae50bad82cf` (4 August 2026) |
| License | MIT |
| What we use it for | NIC selection on the switchless triangle |

Build it once per node and keep the result next to the engine:

```
git clone https://github.com/autoscriptlabs/nccl-mesh-plugin.git && cd nccl-mesh-plugin && git checkout 19924dcc7c571d6e260953724d394ae50bad82cf && make
```

The build produces `libnccl-net-mesh.so`; NCCL loads it under the name `libnccl-net.so`,
so keep both names in the directory:

```
mkdir -p ~/glm3x/nccl-mesh && cp libnccl-net-mesh.so ~/glm3x/nccl-mesh/ && ln -sf libnccl-net-mesh.so ~/glm3x/nccl-mesh/libnccl-net.so
```

That directory is bind-mounted into the container at `/opt/nccl-mesh`, and the engine
environment selects it with `NCCL_NET=Mesh`, `NCCL_NET_PLUGIN=mesh` and
`LD_LIBRARY_PATH=/opt/nccl-mesh`. The full env file is documented with the engine flags.

Two things to get right, both of which cost real bandwidth if you get them wrong:

- **`NCCL_IB_MERGE_NICS=1` is mandatory.** Each cable carries two logical ports. Without
  the merge, NCCL uses one of them and you lose half the fabric.
- **Do not pin `NCCL_IB_GID_INDEX`.** The widely-copied `NCCL_IB_GID_INDEX=3` line is
  correct for a two-node single-subnet link and is wrong on a pairwise triangle — leave
  NCCL to pick (the default, -1). Using `-x 3` with `ib_write_bw` for a single manually
  configured link is a different matter and is fine.

**Open item, stated plainly:** the binary running on our nodes is dated 29 August 2026 and
we did not record which commit produced it. The commit above is the one in our local
clone. The two are almost certainly the same thing, and we have not proved it
`[measured-here, raw lost]`. If you build from the commit above and get a different
`sha256sum` from a binary of ours, ours is the unverified one.

## 4. Fabric preflight

The RDMA fabric is the single most common reason this cluster fails to start, and the
failure is quiet. **`ip -br link` showing UP does not mean the link works. Ping succeeding
does not mean the link works.** The only check that counts:

```
ibv_devinfo | grep -c PORT_ACTIVE
```

This must print `4` on every node. Anything less and the engine will die at startup with
`NCCL error: unhandled system error` after several minutes of loading weights.

If you want a stronger check, measure both directions with `ib_write_bw` — a link can be
`PORT_ACTIVE` and still be running at half width on old SBIOS (see
[00-prerequisites.md](00-prerequisites.md), section 2).

## 5. The hotplug trap: reboot all three, together

**Symptom.** Reboot one node on its own. It comes back. The fabric does not: the port at
the *other* end of its cables goes down and never comes back by itself. `ibv_devinfo`
shows 2 active ports instead of 4. The dead interfaces often have no address either,
because their NetworkManager connection did not activate at boot.

**What we saw on 1 September 2026** `[measured-here]`:

| Action | Result |
|---|---|
| Reboot all three | two links `worker-1` ↔ `worker-2` dead |
| Reboot `worker-1` + `worker-2` | that pair recovered, `head` ↔ `worker-1` died |
| `nmcli con up` on the dead interfaces | address returned, carrier still 0 |
| `ip link down/up` at both ends simultaneously | no effect |
| **Reboot all three together** | **4/4, full mesh** |

**Root cause, found 2 September 2026.** Not a guess — a package.
`dgx-spark-mlnx-hotplug` installs a udev rule (`90-mtk-hotplug.rules`) that runs
`mtk-hotplug-handler.sh`. If the marker file **`/etc/nvidia/cx7-hotplug-enabled`** exists,
hotplug is armed, and the handler **removes the ConnectX-7 card from PCI the moment the
peer at the other end of the cable goes down**. That is why the surviving node's port
never came back: its card had been detached.

**What we did.** Removed the marker file on all three nodes, keeping a backup:

```
sudo mv /etc/nvidia/cx7-hotplug-enabled /root/cx7-hotplug-enabled.backup
```

Verify it is gone on every node:

```
ls -la /etc/nvidia/
```

On our nodes `/etc/nvidia/` is now empty. The corresponding `sysfs` value stays at 1; the
marker file is what arms the handler. The package itself stays installed.

Credit for the pointer: `digchick/dgx-spark-200g-link-fix`. Our contribution is the
diagnosis, the permanent fix and the reboot checklist.

**The rule, which stands even with the marker removed.** If the engine crashed, or you
need to reboot for any other reason, **reboot all three nodes together**. A single-node
reboot kills the peer's port. `drop_caches` and `compact_memory` are not a substitute for
a reboot after a crash — a crashed engine leaves cache and stuck allocations behind, and
we have seen the GPU clock stick at 721 MHz until a clean reboot cleared it.

Verified on 2 September (two triple reboots) and again on 3 September (triple reboot plus
automatic startup): `PORT_ACTIVE` 4/4 every time `[measured-here]`.

**Manual reboot checklist, before starting the engine:**

| # | Check | Why |
|---|---|---|
| 1 | SBIOS is 0105 | older BIOS halves the second QSFP cage silently |
| 2 | GPU clock ≥ 2200 MHz | 611–728 MHz means a power-delivery lock; every measurement taken in that state is garbage and you will only notice afterwards |
| 3 | ConnectX link is x4 | half-width link, no error message |
| 4 | `ibv_devinfo \| grep -c PORT_ACTIVE` = 4 | the actual fabric state |
| 5 | `ib_write_bw` **in both directions** | a link can be active one way and useless the other |

## 6. Power and thermal notes

One of our units (`worker-2`) idles 10 °C hotter than the other two. The cause is not the
chip and not the airflow: **the ASUS embedded controller drives the fan from power draw,
not temperature.** That unit's idle draw sits below the threshold, so the fan does not
spin, so it heats up. Proven in place — after a CPU load the fan kept spinning and the
board dropped to 38 °C, *cooler than idle*; ten minutes later the fan stopped and it
climbed back to 46 °C `[measured-here]`.

- This is documented by other owners of identical units; NVIDIA has not issued a fix. The
  community workaround is to plug in a USB device drawing 5 W or more. **We have not tried
  it** `[not tested]`.
- Under production load the fan spins anyway, so this matters mostly during idle periods
  and for long-term wear.
- The 2.5 % clock spread between our units is a **separate** phenomenon and is not
  explained by heat: at equal temperature with fans running, the slow unit was still
  62 MHz behind. That is normal binning. Plan around it; do not RMA over it.

We also disabled a GPU clock cap service we had installed earlier (it cost 15.9 % of
compute and zero memory bandwidth, and it had not prevented any of our crashes, which were
all memory-related). The real protection is a sane memory fraction, not a clock cap — see
[05-memory-ladder.md](05-memory-ladder.md).

## 7. The preflight script

[`scripts/engine-preflight.sh`](../scripts/engine-preflight.sh) runs before the engine on every
node, as the systemd unit's `ExecStartPre`. It waits up to 10 minutes and refuses to
continue if the cluster is not ready. Four checks, each protecting against something we
actually hit:

| Check | Waits up to | What it protects against |
|---|---|---|
| `docker info` responds | 300 s | at boot, the service can start before the Docker daemon is ready; without the wait the unit fails and nothing restarts it |
| `ibv_devinfo \| grep -c PORT_ACTIVE` = 4 | 600 s | starting the engine on a half-dead fabric. It loads weights for minutes and then dies with `unhandled system error`, which points at nothing |
| ping each fabric neighbour | — | catches interfaces that came up without an address (the NetworkManager failure in section 5). Ping succeeding does not prove RDMA works, but ping failing proves something is wrong |
| `sync; echo 3 > /proc/sys/vm/drop_caches` | — | the page cache left over from reading a 186 GB checkpoint competes with the engine's own allocation. Dropping it first makes startup memory predictable |

The engine service itself starts in parallel on all three nodes. Ordering is not
guaranteed by systemd — the ranks wait for each other at the rendezvous with a long
timeout, which is what actually makes it work.

Measured startup after a triple reboot `[measured-here]`:

| Milestone | Time from reboot |
|---|---|
| SSH answering | 101 s |
| Fabric 4/4 | 103 s |
| Service started | 104 s |
| **Engine serving** | **304 s** |

A production reboot on 3 September took 296 s from power-on to a serving engine.

To stop the engine, on all three nodes:

```
sudo systemctl stop harem-motor
```

---

## Checklist before the first launch

Tick these on every node. If you are handing this to an AI agent, this is the list it
should verify and report on, one line per item, before it touches the engine.

- [ ] Cables wired as the ring in [00-prerequisites.md](00-prerequisites.md), verified with LLDP, not by eye
- [ ] Fabric addresses assigned, each link on its own /24, MTU 9000, `never-default` set
- [ ] `ip -br addr` shows all four fabric interfaces with addresses on every node
- [ ] `ibv_devinfo | grep -c PORT_ACTIVE` prints **4** on every node
- [ ] `ib_write_bw` measured in **both** directions on all three cables
- [ ] `ls /etc/nvidia/cx7-hotplug-enabled` returns "No such file or directory" on **all three** nodes
- [ ] GPU clock ≥ 2200 MHz on every node (`nvidia-smi -q -d CLOCK`)
- [ ] Mesh plugin built and present at `~/glm3x/nccl-mesh/` with both filenames
- [ ] `docker info` works without sudo for the user the service runs as
- [ ] Both checkpoints present at the same paths on all three nodes
- [ ] Everyone who will operate this cluster knows: **reboot all three, together**

Next: the engine image and its flags, then [05-memory-ladder.md](05-memory-ladder.md).
