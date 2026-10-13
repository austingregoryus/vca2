#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
$SSH root@172.31.1.1 'echo === meminfo ===
grep -E "MemTotal|MemFree|MemAvailable|Cma" /proc/meminfo
echo === buddyinfo ===
cat /proc/buddyinfo
echo === compact ===
echo 1 > /proc/sys/vm/compact_memory
sync
echo 3 > /proc/sys/vm/drop_caches
echo === buddy after ===
cat /proc/buddyinfo
grep MemAvailable /proc/meminfo
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_alloc=1 pci_did=0x2958 host_sz=0x2000000
dmesg | grep alut_gmap | tail -6
'
echo ping
ping -c 1 -W 1 172.31.1.1
