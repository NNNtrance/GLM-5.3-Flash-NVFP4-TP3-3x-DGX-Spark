#!/bin/bash
# Engine preflight, run by harem-motor.service as ExecStartPre on every node.
# Waits (at most 10 minutes) until the node can actually serve, then drops the
# page cache so the weight load starts from a clean state.
#
#   1. docker is up and answering
#   2. all four ConnectX-7 ports report PORT_ACTIVE (ibv_devinfo)
#   3. this node can ping both of its fabric neighbours
#   4. sync + drop_caches
#
# Why each check exists: at boot the engine starts long before the fabric is
# ready. Without (2) and (3) the NCCL rendezvous hangs with no useful error and
# the unit sits in "activating" until TimeoutStartSec. Failing here instead is
# loud and tells you which link is missing.
#
# FABRIC_PEERS: the two fabric addresses THIS node must be able to reach. The
# three nodes are wired as a pairwise QSFP triangle, so each node has two
# point-to-point /30-style links and a different pair of neighbours. Set it per
# node in the environment, or fill in the case below with your own addresses.
# The values shown are RFC 5737 documentation addresses — replace them.
#
#   head      FABRIC_PEERS="198.51.100.2 198.51.100.6"
#   worker-1  FABRIC_PEERS="198.51.100.1 198.51.100.10"
#   worker-2  FABRIC_PEERS="198.51.100.5 198.51.100.9"
#
# drop_caches needs root: either run the unit as root, or give the service user
# a NOPASSWD sudoers line for /usr/bin/tee /proc/sys/vm/drop_caches. If it is
# not permitted the script does not fail — it just skips the drop.

PEERS="${FABRIC_PEERS:-}"
if [ -z "$PEERS" ]; then
  case "${NODE_NAME:-$(hostname)}" in
    head)     PEERS="198.51.100.2 198.51.100.6"  ;;
    worker-1) PEERS="198.51.100.1 198.51.100.10" ;;
    worker-2) PEERS="198.51.100.5 198.51.100.9"  ;;
    *)        PEERS="" ;;
  esac
fi

t0=$(date +%s)
until docker info >/dev/null 2>&1; do
  sleep 5
  [ $(( $(date +%s)-t0 )) -gt 300 ] && { echo "docker not ready"; exit 1; }
done

until [ "$(ibv_devinfo 2>/dev/null | grep -c PORT_ACTIVE)" = "4" ]; do
  sleep 5
  [ $(( $(date +%s)-t0 )) -gt 600 ] && {
    echo "ConnectX-7 not 4/4: $(ibv_devinfo 2>/dev/null | grep -c PORT_ACTIVE)/4"; exit 1; }
done

for p in $PEERS; do
  until ping -c1 -W2 "$p" >/dev/null 2>&1; do
    sleep 5
    [ $(( $(date +%s)-t0 )) -gt 600 ] && { echo "fabric peer $p unreachable"; exit 1; }
  done
done

sync; echo 3 | sudo -n tee /proc/sys/vm/drop_caches >/dev/null 2>&1
echo "preflight ok: $(( $(date +%s)-t0 )) s, ConnectX-7 4/4, peers: $PEERS"
