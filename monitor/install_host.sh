#!/bin/bash
# SPDX-License-Identifier: MIT
# Install/refresh the VCA monitor on the Proxmox host. Run from the repo root:
#   bash monitor/install_host.sh
# Keeps an existing /etc/vcamon.env (optional settings, see README) and the DB.
set -euo pipefail
cd "$(dirname "$0")"
install -d /opt/vcamon /var/lib/vcamon
install -m 0755 vcamon.py /opt/vcamon/vcamon.py
ln -sf /opt/vcamon/vcamon.py /usr/local/bin/vcamon
install -m 0644 vcamon.service /etc/systemd/system/vcamon.service
systemctl daemon-reload
systemctl enable --now vcamon.service
systemctl restart vcamon.service
sleep 2
systemctl is-active vcamon.service
