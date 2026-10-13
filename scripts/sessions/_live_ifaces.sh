#!/bin/bash
# SPDX-License-Identifier: MIT
# Read-only inventory of VCA userspace/sysfs. Do NOT cat dma_test*.
set -u
echo '=== host /dev ==='
ls -l /dev/vca* /dev/vop* 2>/dev/null
echo
echo '=== host sysfs class/vca ==='
find /sys/class/vca /sys/class/vop /sys/devices -path '*vca*' -o -path '*vop-dev*' 2>/dev/null | head -80
echo
echo '=== vop-dev00 attrs ==='
ls -l /sys/bus/vop_bus/devices/vop-dev00/
echo
echo '=== safe vop1 files (names only) ==='
ls -l /sys/kernel/debug/vop1/
echo '--- vop1 stats ---'
cat /sys/kernel/debug/vop1/stats
echo
echo '=== plx debugfs names only ==='
ls /sys/kernel/debug/plx87xx/0000:05:00.0/ 2>/dev/null
ls /sys/kernel/debug/plx87xx_dma/0000:03:00.2/ 2>/dev/null
echo
echo '=== /dev/vca00 ioctls via strings? skip ==='
echo '=== host mmap? ==='
ls -l /sys/bus/pci/devices/0000:05:00.0/resource*
echo
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== node0 /dev/vca sysfs ==='
$SSH root@172.31.1.1 'ls -l /dev/vca*; ls -l /sys/class/vca/vca/; echo --- state ---; cat /sys/class/vca/vca/state; echo --- net_config ---; xxd /sys/class/vca/vca/net_config | head; echo --- sys_config ---; xxd /sys/class/vca/vca/sys_config | head; echo --- csa_mem ---; ls -l /sys/class/vca/vca/csa_mem; xxd /sys/class/vca/vca/csa_mem | head; echo --- pci ntb ---; lspci -d 8086:2958 -vv 2>/dev/null | head -40; echo --- resource ---; cat /sys/bus/pci/devices/0000:01:00.0/resource; ls -l /sys/bus/pci/devices/0000:01:00.0/resource*; echo --- debugfs names ---; ls /sys/kernel/debug 2>/dev/null | head'
