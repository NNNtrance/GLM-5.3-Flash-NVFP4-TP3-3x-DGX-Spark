# 00 — Prerequisites

What you need before you build anything: three machines, the versions we ran, the
system-level decisions we made (desktop off, swap left alone), and the two model
downloads.

Everything below was read off our own three nodes on 4 September 2026 unless marked
otherwise. Node names in this repository are always `head` (rank 0, serves the API),
`worker-1` and `worker-2`.

---

## 1. Hardware

| Item | What we have |
|---|---|
| Nodes | 3 × NVIDIA DGX Spark (GB10, `sm_121`, aarch64). Ours are ASUS Ascent GX10 units |
| Memory | 128 GB unified per node; the OS sees **121 GiB**, and the engine sees 111.5–111.8 GiB free on the device (see [05-memory-ladder.md](05-memory-ladder.md)) |
| Storage | ~916 GB NVMe per node |
| Interconnect | 2 ConnectX-7 QSFP cages per node, **no switch** — three direct cables in a ring |

### Fabric topology

Three cables, one per pair, wired as a directed ring. NVIDIA names the cages by position:
**Port0** is the QSFP next to the ethernet jack, **Port1** is the far one. Each node's near
cage goes to the next node's far cage:

| Cable | From | To |
|---|---|---|
| 1 | `head` Port0 | `worker-1` Port1 |
| 2 | `worker-1` Port0 | `worker-2` Port1 |
| 3 | `worker-2` Port0 | `head` Port1 |

Each physical cage carries **two logical ports**, so each node exposes 4 network
interfaces and 4 RoCE devices, and the three cables carry six point-to-point links.
Every link sits on its own /24 — there is no single RoCE subnet on a three-node triangle.

Measured with `ib_write_bw` (RoCEv2): **98.0 Gb/s per logical link, 196.0 Gb/s per cable,
588 Gb/s aggregate fabric** — 98 % of line rate, identical on all three cables
`[measured-here]`. TCP over the same cables reached 24.7 GB/s per cable with 8 streams
`[measured-here]`. Local NVMe reads 5.8–5.9 GB/s, so the disk, not the fabric, is the
bottleneck when moving the checkpoint around.

Cabling, addressing and the preflight checks live in
[01-cluster-setup.md](01-cluster-setup.md). Check the cabling twice before powering on;
a mis-wired ring produces a network configuration that looks half-alive and fails later.

Source for the ring layout: NVIDIA's own `connect-three-sparks` playbook
(<https://github.com/NVIDIA/dgx-spark-playbooks>).

---

## 2. Software versions we ran

This is the exact system layer under the recipe `[measured-here]`. All three nodes are
identical except where the table says otherwise.

| Component | Version | Command that prints it |
|---|---|---|
| OS | Ubuntu 24.04.4 LTS (noble), aarch64 | `cat /etc/os-release` |
| DGX OS | `dgx-release` 7.5.0 | `dpkg -l \| grep dgx-release` |
| Running kernel | `6.17.0-1029-nvidia` on `head` and `worker-1`, `6.17.0-1031-nvidia` on `worker-2` | `uname -r` |
| Installed kernels | `6.17.0-1014.14` (factory image) and `6.17.0-1029.29` / `6.17.0-1031` | `dpkg -l \| grep linux-image` |
| NVIDIA driver | 580.173.02 (`nvidia-driver-580-open`, apt `hold`) | `nvidia-smi` |
| CUDA toolkit | 13.0 (`cuda-toolkit-13-0` 13.0.3-1) | `dpkg -l \| grep cuda-toolkit` |
| NCCL | 2.29.7 — ships inside the container image, not on the host | container log line `vLLM is using nccl==2.29.7` |
| Docker | 29.2.1, build a5c7197 | `docker --version` |
| ConnectX-7 firmware | 28.45.4028, 4 ports `PORT_ACTIVE` per node | `ibv_devinfo` |
| SBIOS / EC / SoC / USB-C PD | `GX10DGX.0105` (5 May 2026) / `0x02000006` / `0x03000007` / `0x00000516` | `fwupdmgr get-devices` |
| Default systemd target | `multi-user.target` (no desktop) | `systemctl get-default` |
| Swap | `/swap.img`, 16 GB, priority −2, `vm.swappiness` 60 | `swapon --show` and `cat /proc/sys/vm/swappiness` |

Run these on every node, not just one. Our own `worker-2` runs a different kernel from
the other two and we never recorded why — a good reminder that "they are identical" is
an assumption until you print it.

```
cat /etc/os-release
```

```
uname -r
```

```
nvidia-smi
```

```
docker --version
```

```
ibv_devinfo
```

```
dpkg -l | grep -E "dgx-release|linux-image-6|nvidia-driver-580|cuda-toolkit-13"
```

### BIOS 0104 or newer is required

On older SBIOS the second QSFP cage negotiates at half width (Gen5 x2) and you lose
roughly a quarter of the fabric bandwidth without any error message. We updated all
three units to `GX10DGX.0105` with `fwupdmgr` (LVFS, NVIDIA-signed) before measuring.

```
sudo fwupdmgr update --assume-yes
```

Two field notes from doing this `[measured-here]`:

- Do not reboot while an update is still running. Wait until
  `ps -eo cmd | grep -c "[f]wupdmgr update"` returns 0.
- The USB-C PD firmware is **not** applied by a reboot — the controller lives inside the
  240 W adapter and a reboot does not cut its power. If you see
  `expected 0x00000516 and got 0x00000001`, do this instead: shut down, unplug wall and
  device and USB-C, hold the power button 30 s, plug back in, boot, **re-install the
  update**, then reboot. A cold drain alone is not enough.

---

## 3. Update first

**Our recipe was built and tested on the versions in the table above. Older kernels and
drivers are untested.** Before you start, bring all three nodes up to date and reboot
them together.

On each node:

```
sudo apt update
```

```
sudo apt full-upgrade -y
```

Then reboot **all three at the same time** — see
[01-cluster-setup.md](01-cluster-setup.md) for why a single-node reboot breaks the fabric.

```
sudo reboot
```

After the reboot, verify the fabric before anything else:

```
ibv_devinfo | grep -c PORT_ACTIVE
```

That must print `4` on every node.

**Honest note about our own update history.** We updated these machines when we first
set them up (20 August 2026) and we did not record the steps. The apt and dpkg logs have
since rotated, so we cannot reconstruct which commands were run, in what order, or the
before/after versions `[measured-here, raw lost]`. What we can state is where the
machines ended up, which is the table in section 2. If your starting point is the factory
image (`6.17.0-1014` on our units), a plain `apt full-upgrade` is what we would do today.

---

## 4. Desktop session: we run without one

All three nodes boot to `multi-user.target`. `graphical.target` is inactive and the GNOME
packages are still installed — the desktop is not removed, it simply never starts
`[measured-here]`.

**If you reach the nodes over SSH only, you do not need the desktop.** Every step in this
recipe is a shell command.

The memory this frees is **2–3 GiB per node `[estimate]`** — we did not measure it. We
never ran these machines with a desktop session up, so we have no before/after `free -g`
reading to quote. Treat the number as a rough expectation, not a result. If you want the
real figure on your hardware, measure it: `free -g` with the desktop running, switch,
reboot, `free -g` again.

Switch the desktop off:

```
sudo systemctl set-default multi-user.target
```

```
sudo reboot
```

Switch it back on:

```
sudo systemctl set-default graphical.target
```

```
sudo reboot
```

`systemctl get-default` tells you which one is armed. Nothing is uninstalled either way,
so the switch is reversible in both directions.

---

## 5. Swap: leave it alone

Our nodes carry a 16 GB swap file at default priority with `vm.swappiness = 60`, and we
left both untouched. There is no persistent sysctl file on any node.

```
swapon --show
```

```
cat /proc/sys/vm/swappiness
```

**Do not set `vm.swappiness=0` on this stack.** On 2 September 2026 we changed it from 60
to 0 while chasing an unrelated allocation problem. The next production start — the
tightest configuration we run, with a large pinned KV reservation — **locked all three
machines simultaneously**: ping answered, TCP port 22 accepted, no SSH banner, no console.
A physical power cycle on all three was the only way out. The same configuration had
passed 7/7 two hours earlier, and `swappiness` was the only system setting that changed in
between `[measured-here, raw lost]` — the machines died before anything was written to
disk, so we have no logs and the causal link is **not proven**. It is one incident with
one strong suspect.

The advice we had followed (`swappiness=0`) came from a two-node setup with a 3 GiB KV
reservation. It was good advice for that machine and it was not good advice for ours.

The rule we now work by: **a kernel or system setting is tried first on a low-KV test
branch, never for the first time in production.** Production is the tightest condition you
own; it is the worst place to learn what a setting does.

---

## 6. Model downloads

Two checkpoints, both fetched on every node (the engine loads from local disk on all
three ranks — there is no shared filesystem in this recipe).

Install the Hugging Face CLI first:

```
pip install -U "huggingface_hub[cli]"
```

### Target model — 186 GB

`local-inference-lab/GLM-5.3-Flash-NVFP4`, an NVFP4 mixed-precision quantization of
`zai-org/GLM-5.3-Flash`. Pin the revision:

```
hf download local-inference-lab/GLM-5.3-Flash-NVFP4 --revision 9c712132678ee8ec869db9f848042ab8314c7685 --local-dir /var/tmp/glm-5.3-flash-lil-nvfp4
```

186 GB, 36 safetensors shards. The repository ships a `SHA256SUMS` file; all 49 entries
verify on all three of our nodes, and those sums match revision
`9c712132678ee8ec869db9f848042ab8314c7685` `[measured-here]`. Check it yourself after the
download:

```
cd /var/tmp/glm-5.3-flash-lil-nvfp4 && sha256sum -c SHA256SUMS
```

### Draft model — 2.2 GB

`incoai/GLM-5.3-Flash-DFlash2`, used for speculative decoding at k=7.

```
hf download incoai/GLM-5.3-Flash-DFlash2 --revision dc77ff1c --local-dir /var/tmp/dflash2-draft
```

**Use revision `dc77ff1c`, not the newer `bf582e4e`.** We ran both. The newer revision
produced no measurable gain, and on one task it produced a repeatable wrong answer that
the older revision got right. The full comparison is in [08](08-what-we-tried.md).

The draft's `config.json` also needs a shape change for TP=3; that is part of the engine
setup, not the download.

### Licenses

The target checkpoint is MIT (Z.AI). **The DFlash2 draft is `cc-by-nc-nd-4.0`.** We
obtained a project-specific, non-transferable permission from its authors for our own use;
that permission does not extend to you, and we do not redistribute the draft. If you want
to use it, obtain your own permission. Every component, its exact revision and its license
are listed in [LICENSES.md](../LICENSES.md).

### Disk space per node

| Item | Size |
|---|---|
| Target checkpoint | 186 GB |
| Draft model | 2.2 GB |
| Base container image | 30.7 GB |
| Image chain we build on top | ~15 GB of additional layers |
| Working room (build cache, logs, results) | 20 GB |

Our nodes show 269 GB used and 601 GB free of 916 GB with the whole stack in place
`[measured-here]`. **Plan for at least 300 GB free per node before you start.**

We keep both models under `/var/tmp` because that is where they landed on day one and the
path is baked into our env files. `/var/tmp` is not cleaned on these systems, but it is
not a good long-term home either — if you are starting fresh, put them somewhere you
control and adjust the mount paths in the engine configuration.

---

## Checklist before moving on

- [ ] Three nodes, same OS and driver, updated and rebooted **together**
- [ ] SBIOS 0104 or newer on all three
- [ ] `ibv_devinfo | grep -c PORT_ACTIVE` prints 4 on all three
- [ ] `systemctl get-default` prints `multi-user.target` (or you accept the desktop's memory cost)
- [ ] `cat /proc/sys/vm/swappiness` prints 60 — do not change it
- [ ] Both checkpoints downloaded at the pinned revisions and `sha256sum -c` passes
- [ ] At least 300 GB free per node

Next: [01-cluster-setup.md](01-cluster-setup.md).
