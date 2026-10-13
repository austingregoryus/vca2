#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

echo '=== temps before ==='
vcactl temp 2>/dev/null | grep -E 'Card|Core'

echo '=== rebuild/restart daemons ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_daemon.c root@$ip:/tmp/vca_compute/cl_daemon.c
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
gcc -O2 -o /tmp/vca_compute/cl_daemon /tmp/vca_compute/cl_daemon.c -ldl
if [ -f /tmp/vca_compute/daemon.pid ]; then
  kill "$(cat /tmp/vca_compute/daemon.pid)" 2>/dev/null || true
  sleep 0.3
fi
/tmp/vca_compute/cl_daemon >/tmp/vca_compute/daemon.log 2>&1 &
echo $! > /tmp/vca_compute/daemon.pid
sleep 0.7
cat /tmp/vca_compute/daemon.log
EOF
done

python3 /tmp/vca_compute/gpu_kern.py

echo '=== temps after ==='
vcactl temp 2>/dev/null | grep -E 'Card|Core'
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'echo pkg=$(cat /sys/class/thermal/thermal_zone2/temp); echo gt=$(cat /sys/class/drm/card0/gt_cur_freq_mhz) MHz'
