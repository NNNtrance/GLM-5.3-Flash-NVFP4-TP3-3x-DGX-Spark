#!/bin/bash
# The measuring half of the memory ladder. Run it with the engine UP.
#
#   ./memory-ladder-measure.sh <label>          e.g. ./memory-ladder-measure.sh 0.88
#
# Order matters: idle memory and swap FIRST (before any load touches them),
# then the quality gate, then the speed probes, then memory and swap again
# under load. The closing line prints the KV pool the engine reported at start
# and the lowest free memory seen across the three nodes.
#
# What to expect at the production setting (gpu-memory-utilization 0.88,
# image harem/glm53-lil:t10, TP=3 + EP + marlin, DFlash2 k=7, graphs on,
# thinking low, temperature 0) -- see audit/README.md for the full table:
#   KV pool           ~4,321,739 tokens
#   correctness       10/10
#   cold/warm C1      warm 43-47 tok/s, acceptance 44-48%
#   free memory       head 4.6-4.7 GB, workers 6.8-6.9 GB, head swap ~450 MB
set -u
D="$(cd "$(dirname "$0")" && pwd)"
. "$D/lib-cluster.sh"
LABEL=${1:?usage: memory-ladder-measure.sh <label>}

swap()   { for h in $NODES; do echo -n "  $h swap: "; $SSH "$h" "free -m | grep Swap | tr -s ' '"; done; }
minfree(){ for h in $NODES; do $SSH "$h" "grep MemAvailable /proc/meminfo"; done \
             | tr -s ' ' | cut -d' ' -f2 | sort -n | head -1 | awk '{printf "%.1f", $1/1048576}'; }

KV=$($SSH "$HEAD_HOST" "docker logs $CONTAINER 2>&1 | grep -oE 'GPU KV cache size: [0-9,]+' | head -1 | grep -oE '[0-9,]+$'")

echo "  memory (idle):";       bash "$D/free-memory-snapshot.sh" "$LABEL"; swap
echo "  correctness:";         python3 "$D/correctness-probe.py" "$API" 2>&1 | grep -E "both fields|/10" | tail -2 | sed 's/^/    /'
echo "  C1 cold/warm:";        python3 "$D/cold-warm-c1.py"      "$API" 2>&1 | tail -1 | sed 's/^/    /'
echo "  category C1+C4:";      python3 "$D/category-speed.py"    "$API" 2>&1 | grep -E "^  C" | sed 's/^/  /'
echo "  memory (under load):"; bash "$D/free-memory-snapshot.sh" "$LABEL-load"; swap
echo "=== STEP $LABEL DONE $(date +%H:%M:%S) KV=$KV lowest_free=$(minfree)GiB"
