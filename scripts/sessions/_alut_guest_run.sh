#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'
PEER=$(awk '/peer_off/{print $2}' /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info)
echo "peer=$PEER"
$SCP /tmp/vca_compute/alut/alut_guest.c root@172.31.1.1:/tmp/alut_guest.c
$SSH root@172.31.1.1 "gcc -O2 -o /tmp/alut_guest /tmp/alut_guest.c && /tmp/alut_guest /sys/bus/pci/devices/0000:01:00.0/resource2 $PEER"
echo guest_exit:$?
echo '=== host header after guest ==='
/tmp/alut_host /dev/vca_alut_0000_05_00_0
echo '=== ping still works? ==='
ping -c 1 -W 2 172.31.1.1
vcactl status | head -5
