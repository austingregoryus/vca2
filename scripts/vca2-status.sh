#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -u

printf '=== Host kernel ===\n'
uname -r

printf '\n=== VCA status ===\n'
/usr/sbin/vcactl status 2>&1 || true

printf '\n=== VCA temperatures ===\n'
/usr/sbin/vcactl temp 2>&1 || true

printf '\n=== BlockIO ===\n'
/usr/sbin/vcactl blockio list 2>&1 || true

printf '\n=== VCA/VOP modules ===\n'
lsmod | grep -Ei 'vca|vop|plx' || true

printf '\n=== VCA virtio devices ===\n'
ls -la /sys/bus/vca_virtio/devices/ 2>/dev/null || true

printf '\n=== VCA node routes/reachability ===\n'
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  if ping -c 1 -W 1 "$ip" >/dev/null 2>&1; then
    echo "$ip reachable"
  else
    echo "$ip NOT reachable"
  fi
done
