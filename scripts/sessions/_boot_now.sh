#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
echo "kernel=$(uname -r)"
lsmod | grep '^plx87xx '
ls /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info 2>/dev/null && echo UNEXPECTED_MBOX || echo backup_driver_ok
vcactl status

echo '=== blockio + boot ==='
for cpu in 0 1 2; do
  vcactl blockio open 0 "$cpu" vcablk0 RW "/tank-red/vmdata/vca/images/node${cpu}.vcad" || true
done
vcactl blockio list
for cpu in 0 1 2; do
  vcactl boot 0 "$cpu" vcablk0
done
vcactl wait
vcactl status
for cpu in 0 1 2; do
  /usr/sbin/vca_eth_ifup.sh 0 "$cpu" || true
done
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  ping -c 2 -W 2 "$ip" || true
done
vcactl temp | grep -E 'Card|Core' | head -20
qm start 100 || true
qm start 101 || true
qm list
echo BOOT_OK
