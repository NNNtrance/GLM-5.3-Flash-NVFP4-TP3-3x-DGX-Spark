#!/bin/bash
# One line per run: available / used / total host memory and container RSS on
# all three nodes.
#
#   ./free-memory-snapshot.sh <label>
#
# On a GB10 the GPU has no memory of its own — it shares the host's. So
# "free memory" here IS the safety margin: when gpu-memory-utilization goes up,
# this number goes down, and the binding limit for the whole recipe is this
# number staying at or above about 2 GiB with only a token amount of swap.
# Read it together with the swap column in memory-ladder-measure.sh.
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib-cluster.sh"
LABEL=${1:-?}
out=""
for h in $NODES; do
  r=$($SSH "$h" 'a=$(awk "/MemAvailable/ {printf \"%.1f\", \$2/1048576}" /proc/meminfo);
       t=$(awk "/MemTotal/ {printf \"%.0f\", \$2/1048576}" /proc/meminfo);
       u=$(awk "/MemTotal/ {tot=\$2} /MemAvailable/ {av=\$2} END {printf \"%.1f\", (tot-av)/1048576}" /proc/meminfo);
       c=$(docker stats --no-stream --format "{{.MemUsage}}" '"$CONTAINER"' 2>/dev/null | cut -d/ -f1 | tr -d " ");
       echo "$(hostname):available=${a}G/used=${u}G/total=${t}G/container=${c:-?}"')
  out="$out $r"
done
echo "  [$LABEL] free-memory=$out"
