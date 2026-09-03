# systemd — start the engine at boot

The engine is a three-node job. Nobody wants to log into three machines after
every power cut, so each node runs the same unit, `harem-motor.service`, and the
cluster comes back on its own. `docs/04-autostart.md` explains why it is built
this way; this page is the install and operate reference.

Boot to serving, measured on this cluster: **296–304 s** `[measured-here]`
(three-node reboot 3 Sep 2026: ssh back at +101 s, ConnectX-7 4/4 at +103 s,
unit started at +104 s, engine answering `/health` at +304 s; a second
production reboot the same day: 296 s).

## The unit, line by line

```ini
Type=oneshot
RemainAfterExit=yes
```

`start-lil.sh` launches a detached container and exits. Without
`RemainAfterExit=yes` systemd would treat that exit as the service having
stopped and `ExecStop` would never run.

```ini
Requires=docker.service
After=docker.service network-online.target
```

Ordering only gets us so far — `docker.service` being "started" does not mean
the daemon is answering yet, and `network-online.target` says nothing about the
ConnectX-7 fabric. The real waiting happens in `ExecStartPre`.

```ini
User=glm53
Environment=HOME=/home/glm53
```

A dedicated unprivileged user that is a member of the `docker` group. `HOME`
must be set explicitly: systemd does not set it for you, and `start-lil.sh`
resolves `$HOME/glm3x/nccl-mesh` when `NCCL_MESH_PLUGIN_DIR` is left at its
default. Adjust both lines, `WorkingDirectory` and the three script paths if
you install somewhere else.

```ini
Environment=ENV_FILE=/home/glm53/glm3x/.env.lil-t10
```

This is how the rank gets in. `start-lil.sh` normally takes the rank as its
first argument, but under systemd there is no argument — it sources `ENV_FILE`
and reads `NODE_RANK` from there. **Each node needs its own env file**: the
three copies differ in `NODE_RANK`, `HOST_IP` and (if the install path differs)
`NCCL_MESH_PLUGIN_DIR`. Copying one node's file to another is the single
easiest way to break this cluster — two ranks announce themselves as rank 0 and
the rendezvous hangs with no useful error. Derive each copy with `sed`; see the
header of `scripts/env.example`.

```ini
ExecStartPre=/bin/bash /home/glm53/glm3x/scripts/engine-preflight.sh
```

The preflight, and the reason autostart works at all. At boot the engine is
ready long before the fabric is. It waits, for at most ten minutes, for:

1. **docker up** — `docker info` answering (5 min cap).
2. **ConnectX-7 4/4** — `ibv_devinfo | grep -c PORT_ACTIVE` equal to `4`. Fewer
   than four active ports means at least one leg of the triangle is missing, and
   the NCCL rendezvous will hang rather than fail.
3. **fabric peers reachable** — a `ping` to each of this node's two fabric
   neighbours. Ports can be `PORT_ACTIVE` while the addressing is not up yet.
4. **`sync` + `drop_caches`** — the weight load starts from a clean page cache.

Set `FABRIC_PEERS` per node (or edit the `case` block in the script) with the
two fabric addresses that node must reach. `drop_caches` needs root: either run
the unit as root, or give the service user a NOPASSWD sudoers entry for
`/usr/bin/tee /proc/sys/vm/drop_caches`. If it is not permitted the preflight
skips the drop instead of failing.

```ini
ExecStop=/usr/bin/docker stop -t 60 harem_glm53_lil
```

`docker stop` with a 60-second grace period, not `kill`. The engine holds ~62
GiB of mapped weights and a multi-process worker group; a hard kill leaves
shared-memory segments behind that the next start then trips over.

```ini
TimeoutStartSec=900
```

Fifteen minutes: ten for the preflight worst case plus the launch itself. Our
measured boot-to-serving is about five minutes, but a cold kernel cache after a
long shutdown is slower.

> The names `harem-motor`, `harem_glm53_lil` and `harem/glm53-lil:t10` are the
> service, container and image names used throughout this stack. Renaming them
> requires care — see the repository README.

## Install (on every node)

```bash
sudo cp systemd/harem-motor.service /etc/systemd/system/harem-motor.service
```

```bash
sudo systemctl daemon-reload
```

```bash
sudo systemctl enable harem-motor
```

Before enabling, check on each node that the paths in the unit exist, that the
env file is that node's own, and that a dry run produces the command line you
expect:

```bash
DRY_RUN=1 ENV_FILE=$HOME/glm3x/.env.lil-t10 $HOME/glm3x/scripts/start-lil.sh
```

## Operate

Start everywhere (workers first, head last — the helper does this for you):

```bash
scripts/engine-start.sh
```

Stop everywhere:

```bash
scripts/engine-stop.sh
```

Stop everywhere and do not start at next boot:

```bash
scripts/engine-stop.sh disable
```

By hand, one node at a time — remember that the workers must be up before the
head, and that stopping only one node is not a state worth being in:

```bash
sudo systemctl start harem-motor
```

```bash
sudo systemctl stop harem-motor
```

Watch a start:

```bash
journalctl -u harem-motor -f
```

```bash
docker logs -f harem_glm53_lil
```

The preflight prints one line when it clears, for example
`preflight ok: 47 s, ConnectX-7 4/4, peers: ...`. If the unit sits in
`activating` for minutes with no such line, the fabric is the thing to look at,
not the engine.

## Rebooting

Reboot **all three nodes together**. A single-node reboot takes down the peer's
fabric port with it, so the two survivors lose a leg of the triangle and the
engine that was still running goes with them. `docs/04-autostart.md` has the
detail.
