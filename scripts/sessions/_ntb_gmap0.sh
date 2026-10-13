#!/bin/bash
# SPDX-License-Identifier: MIT
# Build guest alut_gmap and insmod do_map=0 (DRAM peek only). Then ping.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

mkdir -p /tmp/vca_compute/alut_gmap
cp /tmp/vca_compute/alut_gmap.c /tmp/vca_compute/alut_gmap/alut_gmap.c
cp /tmp/vca_compute/Makefile.guest /tmp/vca_compute/alut_gmap/Makefile

$SSH root@172.31.1.1 'rm -rf /tmp/alut_gmap; mkdir -p /tmp/alut_gmap'
$SCP /tmp/vca_compute/alut_gmap/alut_gmap.c /tmp/vca_compute/alut_gmap/Makefile root@172.31.1.1:/tmp/alut_gmap/

$SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
ls -l
make
ls -l alut_gmap.ko
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=0
dmesg | grep alut_gmap | tail -10
rmmod alut_gmap
echo GMAP0_DONE
'
echo "=== ping ==="
ping -c 2 -W 1 172.31.1.1
echo GMAP0_HOST_DONE
