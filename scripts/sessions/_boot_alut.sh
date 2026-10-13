#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
echo "kernel=$(uname -r) up=$(uptime -p)"
ls -l /lib/modules/$(uname -r)/updates/dkms/plx87xx.ko
ls /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_*
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info || true
zpool status tank-red >/dev/null
vcactl status || true

echo '=== reset + BIOS ==='
vcactl reset || true
vcactl wait-BIOS
vcactl status
for cpu in 0 1 2; do
  vcactl blockio open 0 "$cpu" vcablk0 RW "/tank-red/vmdata/vca/images/node${cpu}.vcad" || true
done
for cpu in 0 1 2; do
  vcactl boot 0 "$cpu" vcablk0
done
vcactl wait
vcactl status
for cpu in 0 1 2; do
  /usr/sbin/vca_eth_ifup.sh 0 "$cpu" || true
done
ping -c 1 -W 2 172.31.1.1
ping -c 1 -W 2 172.31.2.1
ping -c 1 -W 2 172.31.3.1
echo NODES_UP
