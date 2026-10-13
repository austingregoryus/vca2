#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== host vcactl temp ==='
vcactl temp 2>/dev/null | grep -E 'Card|Core'
echo
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "==== $ip sensors ==="
  $SSH root@$ip 'echo -- thermal --; ls /sys/class/thermal 2>/dev/null; cat /sys/class/thermal/thermal_zone*/type /sys/class/thermal/thermal_zone*/temp 2>/dev/null
echo -- hwmon --; ls /sys/class/hwmon 2>/dev/null
for h in /sys/class/hwmon/hwmon*; do echo $h $(cat $h/name 2>/dev/null); ls $h | grep -i temp; cat $h/temp*_input 2>/dev/null; done
echo -- i915 --
ls /sys/class/drm/card0/ 2>/dev/null | head
cat /sys/class/drm/card0/gt_cur_freq_mhz /sys/class/drm/card0/gt_max_freq_mhz 2>/dev/null
grep -r . /sys/class/drm/card0/device/hwmon 2>/dev/null | head
echo -- energy --
cat /sys/kernel/debug/dri/0/i915_energy_uJ 2>/dev/null
cat /sys/class/powercap/*/energy_uj 2>/dev/null | head'
done
