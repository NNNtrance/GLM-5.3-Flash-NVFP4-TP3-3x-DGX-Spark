#!/usr/bin/env bash
# Ship one already built image tag to both workers over the QSFP fabric.
# ("dagit" is Turkish for "distribute"; used by the t3 and t3d layers, which
# have no *-kur.sh wrapper of their own.)
#
#   BUILD_DIR=~/glm53-build WORKER_1=192.0.2.11 WORKER_2=192.0.2.12 ./dagit.sh t3
#
# Prefer the fabric addresses over the management LAN: docker save | docker load
# moves ~15-48 GB and the 200G links do it in 2-3 minutes per worker.
set -uo pipefail

TAG="${1:?usage: dagit.sh <image tag, e.g. t3>}"
BUILD_DIR="${BUILD_DIR:-$HOME/glm53-build}"
WORKER_1="${WORKER_1:-192.0.2.11}"
WORKER_2="${WORKER_2:-192.0.2.12}"
SSH_USER="${SSH_USER:-$USER}"
IMAGE="harem/glm53-lil:${TAG}"
LOG="$BUILD_DIR/dagit-${TAG}.log"
: >"$LOG"

for t in "$SSH_USER@$WORKER_1" "$SSH_USER@$WORKER_2"; do
  echo "=== $t $(date -Is)" >>"$LOG"
  docker save "$IMAGE" \
    | ssh -o BatchMode=yes -o ConnectTimeout=15 -c aes128-gcm@openssh.com "$t" docker load >>"$LOG" 2>&1
  echo "rc=${PIPESTATUS[0]}/$? for $t $(date -Is)" >>"$LOG"
done
echo "DONE $(date -Is)" >>"$LOG"
