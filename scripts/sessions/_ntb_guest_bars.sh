#!/bin/bash
# SPDX-License-Identifier: MIT
# READ ONLY on guest PCI. Do not mmap random writeable windows.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== host BAR 05:00.0 ==='
cat /sys/bus/pci/devices/0000:05:00.0/resource
ls -l /sys/bus/pci/devices/0000:05:00.0/resource*
echo '=== host mbox ==='
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info
echo '=== guest pci ==='
$SSH root@172.31.1.1 'lspci -nn | head -20
echo --- 01:00.0 ---
lspci -s 01:00.0 -vvn 2>/dev/null | egrep "Region|LnkSta|MaxPayload|MaxReadReq|Kernel"
echo --- resource ---
for d in /sys/bus/pci/devices/0000:01:00.0; do
  echo $d
  cat $d/resource
  ls -l $d/resource*
done
echo --- iomem ntb ---
grep -i -E "01:00|pci" /proc/iomem | head -20
'
echo '=== ping ==='
ping -c 1 -W 1 172.31.1.1
