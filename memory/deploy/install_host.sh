#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Make smem and the card survive a host reboot. Run on the host, as root,
# from a checkout of this repository:
#
#   memory/deploy/install_host.sh      install + enable (does not reboot)
#
# Installs:
#   /usr/local/sbin/vca2-boot        <- scripts/vca2-boot.sh
#   /usr/local/sbin/vca2-enable-nat  <- scripts/vca2-enable-nat.sh
#   /usr/local/sbin/vca2-shutdown    <- scripts/vca2-shutdown.sh
#   vca2-card.service (systemd/), smem-api.service (memory/deploy/), enabled
# and the model servers as real units on each node (services.sh install).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(cd .. && pwd)

install -m 0755 "$REPO/scripts/vca2-boot.sh"       /usr/local/sbin/vca2-boot
install -m 0755 "$REPO/scripts/vca2-enable-nat.sh" /usr/local/sbin/vca2-enable-nat
install -m 0755 "$REPO/scripts/vca2-shutdown.sh"   /usr/local/sbin/vca2-shutdown
install -m 0644 "$REPO/systemd/vca2-card.service"  /etc/systemd/system/vca2-card.service
install -m 0644 deploy/smem-api.service            /etc/systemd/system/smem-api.service

# A transient smem-api from `services.sh api` would shadow the unit file.
systemctl stop smem-api 2>/dev/null || true
systemctl reset-failed smem-api 2>/dev/null || true
systemctl daemon-reload
systemctl enable vca2-card.service smem-api.service

# Model servers as installed, enabled units on each node's own disk image.
./services.sh install

# Start both now. vca2-boot is idempotent: with all three nodes already up it
# skips the reset, so this only ARMS vca2-card's clean-shutdown ExecStop for
# this boot -- systemd runs ExecStop only for units that were started.
systemctl start vca2-card.service
systemctl start smem-api.service
systemctl --no-pager --lines=0 status vca2-card.service smem-api.service | grep -E '●|Active'
