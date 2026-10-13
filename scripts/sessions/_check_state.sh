#!/bin/bash
# SPDX-License-Identifier: MIT
echo '=== ping ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  ping -c 1 -W 1 "$ip" | tail -2
  echo
done
echo '=== modules ==='
lsmod | grep -Ei 'plx|vop|^vca' || echo 'NO VCA MODULES'
echo
echo '=== vcactl skip-modprobe ==='
vcactl --skip-modprobe-check status 2>&1 || true
echo
echo '=== dmesg last vca ==='
dmesg | grep -Ei 'plx|vop|vca|dma_hang|Oops' | tail -30
echo
echo '=== procs ==='
ps -eo pid,stat,cmd | grep -E 'vca|plx' | grep -v grep
