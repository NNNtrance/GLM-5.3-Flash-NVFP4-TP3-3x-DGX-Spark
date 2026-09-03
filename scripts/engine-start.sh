#!/bin/bash
# Start the engine on all three nodes and re-enable start-at-boot.
# Run it from the workstation; it only needs ssh and passwordless sudo for
# systemctl on the nodes.
#
#   ./engine-start.sh
#
# Order matters: workers first, head last. The head's rank-0 process is the one
# that runs the rendezvous, and it expects its peers to already be listening.
set -u
. "$(cd "$(dirname "$0")" && pwd)/lib-cluster.sh"

for h in $NODES_REV; do
  $SSH "$h" "sudo -n systemctl enable $SERVICE >/dev/null 2>&1; sudo -n systemctl start $SERVICE; \
             printf '  %-10s started | at boot: %s\n' \"\$(hostname)\" \"\$(systemctl is-enabled $SERVICE)\""
  sleep 3
done
echo "  the engine needs about 5 minutes to serve; check with:"
echo "    curl -s $API/health && echo UP"
