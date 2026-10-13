#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
echo '=== status ==='
vcactl status
echo '=== force reset ==='
vcactl --force reset
sleep 3
vcactl status
echo '=== wait-BIOS 120s ==='
vcactl wait-BIOS
echo after_bios
vcactl status
for cpu in 0 1 2; do
  vcactl blockio open 0 "$cpu" vcablk0 RW /tank-red/vmdata/vca/images/node${cpu}.vcad
done
vcactl blockio list
for cpu in 0 1 2; do
  vcactl boot 0 "$cpu" vcablk0
done
vcactl wait
vcactl status
for cpu in 0 1 2; do
  /usr/sbin/vca_eth_ifup.sh 0 "$cpu" || true
done
ping -c 2 -W 2 172.31.1.1
ping -c 1 -W 2 172.31.2.1
ping -c 1 -W 2 172.31.3.1
ls /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_*
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info
