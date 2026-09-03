#!/bin/bash
# One rung of the memory ladder: stop the engine on all three nodes, rewrite
# GPU_MEMORY_UTILIZATION in each node's env file, start again, wait for health,
# print the KV pool, then hand over to memory-ladder-measure.sh.
#
#   ./memory-ladder-step.sh 0.86
#
# HOW TO USE THE LADDER
# 0.85 is a starting value, not a ceiling. Climb ONE step at a time and stop
# at the last step where the machines still have real headroom. Our measured
# ladder (image harem/glm53-lil:t10, no KV pin, block size 256):
#
#   0.85  KV 3,881,159  head free 6.4-7.6 GB  swap 0        chosen baseline
#   0.86  KV 4,023,188  head free 7.0 GB      swap 41 MB
#   0.87  KV 4,156,521  head free 5.8 GB      swap 47 MB
#   0.88  KV 4,310,144  head free 4.6-4.7 GB  swap 439-456 MB   <- PRODUCTION
#   0.89  KV 4,408,695  head free 5.2 GB      swap 927 MB       REJECTED
#
# 0.89 was rejected because its "free memory" is manufactured by swapping: the
# number goes UP while 927 MB of the machine now lives on disk. The device
# ceiling is around 0.915; the binding limit is host memory, not the GPU.
#
# THE KILL COMMAND -- READ THIS BEFORE EDITING
# Do NOT reach for `pkill -f <name>` to clean up here. `pkill -f` matches
# against the full command line of every process, and the full command line of
# the shell running this script CONTAINS the pattern you just typed. The script
# kills itself, mid-ladder, leaving the cluster in whatever half-configured
# state it had reached. If you ever must pattern-match a process name, break
# the literal so it cannot match itself -- `pkill -f "[l]m_eval"` -- but the
# right answer here is what this script does: stop the systemd unit by name and
# `docker stop` the container by name. Never put the target name into a
# command-line-matching kill.
set -u
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/lib-cluster.sh"
UTIL=${1:?usage: memory-ladder-step.sh <gpu-memory-utilization>, e.g. 0.86}

echo "=== STEP $UTIL started $(date '+%F %H:%M:%S')"

for h in $NODES; do
  $SSH "$h" "sudo -n systemctl stop $SERVICE;
             docker ps -q --filter name=$CONTAINER | xargs -r docker stop -t 60 >/dev/null;
             E=\"$ENGINE_ENV_FILE\";
             [ -f \"\$E.bak-ladder\" ] || cp \"\$E\" \"\$E.bak-ladder\";
             sed -i 's/^GPU_MEMORY_UTILIZATION=.*/GPU_MEMORY_UTILIZATION=$UTIL/' \"\$E\";
             grep ^GPU_MEMORY_UTILIZATION \"\$E\"" | sed "s/^/  $h: /"
done

sleep 5
for h in $NODES_REV; do
  $SSH "$h" "sudo -n systemctl start $SERVICE" && echo "  $h: start issued $(date +%H:%M:%S)"
  sleep 3
done

t0=$(date +%s)
until curl -s -m 5 "$API/health" -o /dev/null; do
  sleep 15
  if [ $(( $(date +%s)-t0 )) -gt 2400 ]; then
    echo "=== STEP $UTIL NO HEALTH after 40 min $(date +%H:%M:%S)"
    for h in $NODES; do
      $SSH "$h" "docker logs --tail 40 $CONTAINER 2>&1 | grep -iE 'error|ValueError|memory' | head -3 | cut -c40-260" | sed "s/^/  $h: /"
    done
    exit 2
  fi
done
echo "  health OK $(date +%H:%M:%S) ($(( $(date +%s)-t0 )) s)"

$SSH "$HEAD_HOST" "docker logs $CONTAINER 2>&1 | grep -E 'GPU KV cache size|Free memory on device|Available KV cache memory' | head -3 | cut -c40-330" | sed 's/^/  /'

sleep 30
bash "$D/memory-ladder-measure.sh" "$UTIL"
