#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'
command -v sshpass >/dev/null || apt-get install -y sshpass >/dev/null

echo '=== mbox_info before ==='
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info

echo '=== alloc node0 only ==='
echo 1 > /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_alloc
echo alloc_exit:$?
dmesg | grep -i 'ALUT mbox' | tail -5
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info
ls -l /dev/vca_alut_*

echo '=== host mmap poke ==='
gcc -O2 -o /tmp/alut_host /tmp/vca_compute/alut/alut_host.c
DEV=$(ls /dev/vca_alut_* | head -1)
echo "dev=$DEV"
/tmp/alut_host "$DEV"
PEER=$(awk '/peer_off/{print $2}' /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info)
echo "peer_off=$PEER"

echo '=== guest mmap ==='
gcc -O2 -o /tmp/vca_compute/alut/alut_guest /tmp/vca_compute/alut/alut_guest.c
$SCP /tmp/vca_compute/alut/alut_guest root@172.31.1.1:/tmp/alut_guest
# start guest poller in background
$SSH root@172.31.1.1 "chmod +x /tmp/alut_guest; ls -l /sys/bus/pci/devices/0000:01:00.0/resource2; /tmp/alut_guest /sys/bus/pci/devices/0000:01:00.0/resource2 $PEER" &
GPID=$!
sleep 1
echo '=== host write ping ==='
/tmp/alut_host "$DEV"
wait $GPID
echo GUEST_EXIT:$?
echo '=== temps ==='
vcactl temp | grep 'Core 0'
echo ALUT_TEST_DONE
