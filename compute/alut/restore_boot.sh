#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
echo '=== restore backup plx87xx.ko ==='
if [ -f /root/plx87xx.ko.bak ]; then
  cp -a /root/plx87xx.ko.bak /lib/modules/$(uname -r)/updates/dkms/plx87xx.ko
  depmod -a
fi
# unload plx only if possible
rmmod plx87xx 2>/dev/null || echo 'plx87xx busy'
/usr/sbin/vca2-load-modules
lsmod | grep plx87xx
ls /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info 2>/dev/null || echo 'mbox gone (backup ko)'
vcactl --force reset
sleep 5
vcactl status
vcactl wait-BIOS
vcactl status
for cpu in 0 1 2; do
  vcactl blockio open 0 $cpu vcablk0 RW /tank-red/vmdata/vca/images/node${cpu}.vcad 2>/dev/null || true
  vcactl boot 0 $cpu vcablk0 2>/dev/null || true
done
vcactl wait
vcactl status
for cpu in 0 1 2; do /usr/sbin/vca_eth_ifup.sh 0 $cpu || true; done
ping -c 2 -W 2 172.31.1.1
echo RESTORE_DONE
