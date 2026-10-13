#!/bin/bash
# SPDX-License-Identifier: MIT
# Health snapshot. No DMA debugfs, no BAR writes.
set -u
echo "=== host ==="
uname -r
uptime
ls /sys/kernel/debug/plx87xx/*/mbox_info 2>/dev/null
for b in 0000:05:00.0 0000:07:00.0 0000:0a:00.0; do
  echo "--- $b ---"
  cat /sys/kernel/debug/plx87xx/$b/mbox_info 2>/dev/null || echo no_mbox
done
vcactl --skip-modprobe-check status 2>/dev/null | head -8
echo "=== pings ==="
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do ping -c 1 -W 1 $ip; done
echo "=== guest pci / headers ==="
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "--- $ip ---"
  $SSH root@$ip 'uname -r; lspci -n | grep 295; ls /lib/modules/$(uname -r)/build >/dev/null && echo headers_ok || echo no_headers'
done
echo "=== temps cores ==="
vcactl temp 2>/dev/null | grep -E "Core [0-9]" | awk '{print} NR==12{exit}'
echo "=== host headers ==="
ls -d /lib/modules/$(uname -r)/build 2>/dev/null || echo no_host_headers
echo LIVE_OK
