#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'
rapl() {
  $SSH root@172.31.1.1 'cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null; cat /sys/class/thermal/thermal_zone2/temp; cat /sys/class/drm/card0/gt_cur_freq_mhz'
}
echo '=== temps BEFORE ==='
vcactl temp 2>/dev/null | grep Core
echo '=== RAPL/pkg/gt node0 BEFORE ==='
rapl
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_daemon.c root@$ip:/tmp/vca_compute/cl_daemon.c
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
gcc -O3 -fopenmp -mavx2 -o /tmp/vca_compute/cl_daemon /tmp/vca_compute/cl_daemon.c -ldl -lm
if [ -f /tmp/vca_compute/daemon.pid ]; then
  kill "$(cat /tmp/vca_compute/daemon.pid)" 2>/dev/null || true
  sleep 0.3
fi
export OMP_NUM_THREADS=4
/tmp/vca_compute/cl_daemon >/tmp/vca_compute/daemon.log 2>&1 &
echo $! > /tmp/vca_compute/daemon.pid
sleep 0.8
cat /tmp/vca_compute/daemon.log
EOF
done
python3 /tmp/vca_compute/gpu_gridpad.py
echo '=== temps AFTER ==='
vcactl temp 2>/dev/null | grep Core
echo '=== RAPL/pkg/gt node0 AFTER ==='
rapl
