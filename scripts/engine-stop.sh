#!/bin/bash
# Stop the engine on all three nodes (maintenance window).
#
#   ./engine-stop.sh              stop now, still starts at next boot
#   ./engine-stop.sh disable      stop now AND do not start at boot
#
# Stopping one node only is not a supported state: the engine is one job across
# three nodes, and a lone survivor holds ~62 GiB of weights for nothing.
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib-cluster.sh"
DISABLE="${1:-}"

for h in $NODES; do
  $SSH "$h" "sudo -n systemctl stop $SERVICE 2>/dev/null; \
             docker rm -f $CONTAINER >/dev/null 2>&1; \
             [ '$DISABLE' = 'disable' ] && sudo -n systemctl disable $SERVICE >/dev/null 2>&1; \
             printf '  %-10s engine: %s | at boot: %s\n' \"\$(hostname)\" \
               \"\$(docker ps --format '{{.Names}}' | grep -c $CONTAINER | sed 's/^0\$/DOWN/;s/^1\$/UP/')\" \
               \"\$(systemctl is-enabled $SERVICE 2>/dev/null)\""
done
