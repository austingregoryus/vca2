#!/bin/bash
# SPDX-License-Identifier: MIT
# Allocate ALUT mbox on node 1 (07:00.0) and node 2 (0a:00.0). Ping after each.
set -eu
echo "=== alloc 07:00.0 (node 1, 32 MiB) ==="
echo 1 > /sys/kernel/debug/plx87xx/0000:07:00.0/mbox_alloc
cat /sys/kernel/debug/plx87xx/0000:07:00.0/mbox_info
dmesg | grep "ALUT mbox" | tail -3
ping -c 1 -W 1 172.31.2.1
echo "=== alloc 0a:00.0 (node 2, 16 MiB) ==="
echo 1 > /sys/kernel/debug/plx87xx/0000:0a:00.0/mbox_alloc
cat /sys/kernel/debug/plx87xx/0000:0a:00.0/mbox_info
dmesg | grep "ALUT mbox" | tail -5
echo "=== pings ==="
ping -c 1 -W 1 172.31.1.1
ping -c 1 -W 1 172.31.2.1
ping -c 1 -W 1 172.31.3.1
vcactl --skip-modprobe-check status | head -5
echo ALLOC12_OK
