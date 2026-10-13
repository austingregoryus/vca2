#!/bin/bash
# SPDX-License-Identifier: MIT
# Enable jumbo 65521 on host eth0/1/2. Guests already 65521.
set -eu
KO=/root/vca2/jumbo_mtu.ko
mkdir -p /tmp/vca_compute/jumbo /root/vca2
cp /tmp/vca_compute/jumbo_mtu.c /tmp/vca_compute/jumbo/jumbo_mtu.c
cp /tmp/vca_compute/jumbo_Makefile /tmp/vca_compute/jumbo/Makefile
( cd /tmp/vca_compute/jumbo && make )
cp /tmp/vca_compute/jumbo/jumbo_mtu.ko "$KO"
rmmod jumbo_mtu 2>/dev/null || true
insmod "$KO" ifnames=eth0,eth1,eth2 mtu=65521
dmesg | grep jumbo_mtu | tail -5
echo "=== host MTU ==="
for n in 0 1 2; do echo -n "eth$n "; cat /sys/class/net/eth$n/mtu; done
echo "=== DF ping 8000 to each node ==="
ping -c 1 -W 1 -s 8000 -M do 172.31.1.1
ping -c 1 -W 1 -s 8000 -M do 172.31.2.1
ping -c 1 -W 1 -s 8000 -M do 172.31.3.1
lsmod | grep jumbo
echo JUMBO_ON
