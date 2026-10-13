#!/bin/bash
# SPDX-License-Identifier: MIT
# One doorbell kick on node 0. Does not cat plx87xx intr_* debugfs.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo "=== health ==="
ping -c 1 -W 1 172.31.1.1
vcactl --skip-modprobe-check status | head -5

echo "=== build host alut_hdb ==="
mkdir -p /tmp/vca_compute/alut_hmap
cp /tmp/vca_compute/alut_hdb.c /tmp/vca_compute/alut_hmap/alut_hdb.c
cp /tmp/vca_compute/Makefile.host /tmp/vca_compute/alut_hmap/Makefile
( cd /tmp/vca_compute/alut_hmap && make )

echo "=== build guest alut_gdb ==="
$SCP /tmp/vca_compute/alut_gdb.c /tmp/vca_compute/Makefile.guest root@172.31.1.1:/tmp/alut_gmap/
$SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
cp Makefile.guest Makefile 2>/dev/null || mv Makefile.guest Makefile
make
rmmod alut_gdb 2>/dev/null || true
insmod ./alut_gdb.ko pci_did=0x2958 db=-1
dmesg | grep alut_gdb | tail -5
'

LINE=$($SSH root@172.31.1.1 'dmesg | grep "alut_gdb: db=" | tail -1')
echo "$LINE"
DB=$(echo "$LINE" | sed -n 's/.*db=\([0-9]*\).*/\1/p')
echo "GUEST_DB=$DB"
if [ -z "$DB" ]; then echo NO_DB; exit 1; fi

echo "=== host insmod kick once ==="
rmmod alut_hdb 2>/dev/null || true
insmod /tmp/vca_compute/alut_hmap/alut_hdb.ko pci_did=0x2954 db=$DB
dmesg | grep alut_hdb | tail -3

echo "=== guest waiter then kick ==="
$SSH root@172.31.1.1 'timeout 3 dd if=/dev/vca_gdb bs=4 count=1 status=none | od -An -tx4' &
GPID=$!
sleep 0.4
echo 1 > /sys/kernel/debug/alut_hdb/kick
wait $GPID || true

echo "=== ping ==="
ping -c 2 -W 1 172.31.1.1
vcactl --skip-modprobe-check status | head -5

echo "=== cleanup ==="
rmmod alut_hdb
$SSH root@172.31.1.1 'rmmod alut_gdb; dmesg | grep alut_gdb | tail -3'
ping -c 1 -W 1 172.31.1.1
echo DB_PROBE_DONE
