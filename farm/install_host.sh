#!/bin/bash
# SPDX-License-Identifier: MIT
# Install/refresh the render farm on the Proxmox host. Run from the repo root:
#   bash farm/install_host.sh
# Keeps an existing /etc/renderfarm.env (and so the client token).
set -euo pipefail
cd "$(dirname "$0")"
install -d /opt/renderfarm
install -m 0644 renderfarm.py /opt/renderfarm/renderfarm.py
install -m 0644 renderfarm.service /etc/systemd/system/renderfarm.service
if [ ! -f /etc/renderfarm.env ]; then
  umask 077
  echo "FARM_TOKEN=$(openssl rand -hex 24)" > /etc/renderfarm.env
  echo "new token written to /etc/renderfarm.env"
fi
for ip in ${FARM_NODES:-172.31.1.1 172.31.2.1 172.31.4.1 172.31.5.1 172.31.6.1}; do
  sshpass -p "${NODE_PW:-vista1}" ssh -o StrictHostKeyChecking=no root@$ip 'bash -s' < node_setup.sh || true
done
systemctl daemon-reload
systemctl enable --now renderfarm.service
systemctl restart renderfarm.service
sleep 2
curl -s http://127.0.0.1:8910/health; echo
