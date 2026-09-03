#!/usr/bin/env bash
# t4b: build -> verify -> ship to both workers -> assert the three image IDs match.
# Run on the head node, from the build directory.
#
#   BUILD_DIR=~/glm53-build WORKER_1=192.0.2.11 WORKER_2=192.0.2.12 ./t4b-kur.sh
#
# "kur" is Turkish for "set up"; the file names are kept as they were built.
set -uo pipefail

BUILD_DIR="${BUILD_DIR:-$HOME/glm53-build}"
WORKER_1="${WORKER_1:-192.0.2.11}"
WORKER_2="${WORKER_2:-192.0.2.12}"
SSH_USER="${SSH_USER:-$USER}"
IMAGE="harem/glm53-lil:t4b"

cd "$BUILD_DIR"
LOG="$BUILD_DIR/build-t4b.log"; : >"$LOG"
D=/var/tmp/t4b-status.txt
echo "T4B BUILDING $(date -Is)" > $D

# Keep the build context down to the two files this layer needs.
cat > Dockerfile.t4b.dockerignore <<'IGN'
*
!t4b/patch_moe_route_ep.py
!t4b/verify_t4b.py
IGN

if ! docker build -f Dockerfile.t4b -t "$IMAGE" . >>"$LOG" 2>&1; then
  { echo "T4B BUILD FAILED (see build-t4b.log)"; tail -20 "$LOG"; } > $D; exit 1
fi

ID=$(docker image inspect "$IMAGE" --format '{{.Id}}' | cut -c8-19)
echo "T4B SHIPPING $ID $(date -Is)" > $D
for t in "$SSH_USER@$WORKER_1" "$SSH_USER@$WORKER_2"; do
  echo "=== $t $(date -Is)" >>"$LOG"
  docker save "$IMAGE" \
    | ssh -o BatchMode=yes -o ConnectTimeout=15 -c aes128-gcm@openssh.com "$t" docker load >>"$LOG" 2>&1
  echo "rc=${PIPESTATUS[0]}/$? for $t $(date -Is)" >>"$LOG"
done

W1=$(ssh -o BatchMode=yes "$SSH_USER@$WORKER_1" "docker image inspect $IMAGE --format '{{.Id}}'" 2>/dev/null | cut -c8-19)
W2=$(ssh -o BatchMode=yes "$SSH_USER@$WORKER_2" "docker image inspect $IMAGE --format '{{.Id}}'" 2>/dev/null | cut -c8-19)
if [ "$ID" = "$W1" ] && [ "$ID" = "$W2" ]; then
  { echo "T4B READY $ID $IMAGE"
    echo "  head/worker-1/worker-2 -> $ID ($(date -Is))"
    echo "  patch: HAREM-B12X-MOE-EP-ROUTE (EP: direct -> auto + zeroed-FC2 route-pack), base harem/glm53-lil:t4"; } > $D
else
  { echo "T4B SHIP FAILED: head=$ID worker-1=$W1 worker-2=$W2"; tail -5 "$LOG"; } > $D
fi
