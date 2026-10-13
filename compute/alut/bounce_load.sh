#!/bin/bash
# SPDX-License-Identifier: MIT
# Graceful VCA bounce to load new plx87xx.ko with ALUT mbox.
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5'
NEW=/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10/plx87xx.ko
DEST=/lib/modules/$(uname -r)/updates/dkms/plx87xx.ko

echo '=== temps ==='
vcactl temp 2>/dev/null | grep 'Core 0' || true

echo '=== guest shutdown ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'sync; shutdown -h now' 2>/dev/null || echo "$ip ssh fail (maybe already down)"
done
sleep 20
vcactl --skip-modprobe-check status || true

echo '=== close blockio ==='
for cpu in 0 1 2; do
  vcactl --force blockio close 0 "$cpu" vcablk1 2>/dev/null || true
  vcactl --force blockio close 0 "$cpu" vcablk0 2>/dev/null || true
done

echo '=== backup and install ko ==='
cp -a "$DEST" /root/plx87xx.ko.bak
cp -a "$NEW" "$DEST"
depmod -a

echo '=== unload stack ==='
# reverse of typical load; ignore failures
for m in plx87xx vca_mgr_extd vca_mgr vca_csm vcablk_bckend vcablkfe vca_virtio_net vop vca_vringh vca_virtio_ring vca_virtio vca_csa_bus vca_mgr_extd_bus vca_mgr_bus vca_csm_bus vop_bus plx87xx_dma; do
  rmmod "$m" 2>/dev/null && echo "rmmod $m" || true
done
lsmod | grep -Ei 'plx|vop|^vca' || echo 'no vca modules'

echo '=== load ==='
/usr/sbin/vca2-load-modules
lsmod | grep plx87xx
sleep 2
ls /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_* 2>/dev/null || echo 'mbox debugfs missing'

echo '=== boot nodes from vcablk0 ==='
vcactl reset || true
vcactl wait-BIOS
for cpu in 0 1 2; do
  vcactl blockio open 0 "$cpu" vcablk0 RW /tank-red/vmdata/vca/images/node${cpu}.vcad || true
done
for cpu in 0 1 2; do
  vcactl boot 0 "$cpu" vcablk0
done
vcactl wait
vcactl status
for cpu in 0 1 2; do
  /usr/sbin/vca_eth_ifup.sh 0 "$cpu" || true
done
ping -c 1 -W 2 172.31.1.1
echo BOUNCE_DONE
