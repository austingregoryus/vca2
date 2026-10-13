#!/bin/bash
# SPDX-License-Identifier: MIT
echo '=== vop1 stats ==='
cat /sys/kernel/debug/vop1/stats
echo
echo '=== vop1 vdev_info ==='
cat /sys/kernel/debug/vop1/vdev_info
echo
echo '=== vop1 dp (first 2k hex) ==='
head -c 512 /sys/kernel/debug/vop1/dp | xxd | head -40
echo
echo '=== plx87xx node0 ==='
ls /sys/kernel/debug/plx87xx/0000:05:00.0/
for f in /sys/kernel/debug/plx87xx/0000:05:00.0/*; do
  echo "-- $(basename $f) --"
  head -c 2000 "$f" 2>/dev/null; echo
done
echo
echo '=== plx dma chans ==='
ls /sys/kernel/debug/plx87xx_dma/0000:03:00.2/
for f in /sys/kernel/debug/plx87xx_dma/0000:03:00.2/*; do
  echo "-- $(basename $f) --"
  head -c 1500 "$f" 2>/dev/null; echo
done
echo
echo '=== node0 /sys/class/vca/vca state/net_config ==='
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'echo state; cat /sys/class/vca/vca/state; echo net_config; cat /sys/class/vca/vca/net_config; echo sys_config; cat /sys/class/vca/vca/sys_config; echo csa_mem; cat /sys/class/vca/vca/csa_mem; ls /sys/kernel/debug 2>/dev/null | grep -Ei "vop|plx|vca|dma"'
