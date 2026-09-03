#!/bin/bash
# Install the autostart unit on THIS node, substituting the invoking user's name and home directory.
# Run on every node: bash systemd/install.sh   (needs sudo; assumes ~/glm3x holds scripts/ and the env file)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
U=${SUDO_USER:-$USER}; H=$(getent passwd "$U" | cut -d: -f6)
[ -d "$H/glm3x/scripts" ] || { echo "expected $H/glm3x/scripts (copy scripts/ there first)"; exit 1; }
sed "s#@USER@#$U#g; s#@HOME@#$H#g" "$HERE/harem-motor.service" | sudo tee /etc/systemd/system/harem-motor.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable harem-motor
echo "installed for user $U (home $H); start now with: sudo systemctl start harem-motor"
