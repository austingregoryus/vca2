#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

echo '=== rebuild cl_bench with tile base on all nodes ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_bench.c root@$ip:/tmp/vca_compute/cl_bench.c
  $SSH root@$ip 'gcc -O2 -o /tmp/vca_compute/cl_bench /tmp/vca_compute/cl_bench.c -ldl'
done

echo '=== tiled SAXPY ==='
python3 /tmp/vca_compute/gpu_tile.py

echo '=== ensure mailbox workers ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'bash -s' <<'EOF'
if [ -f /tmp/vca_compute/worker.pid ] && kill -0 "$(cat /tmp/vca_compute/worker.pid)" 2>/dev/null; then
  echo worker_alive
else
  python3 /tmp/vca_compute/mbox_worker.py >/tmp/vca_compute/worker.log 2>&1 &
  echo $! > /tmp/vca_compute/worker.pid
  sleep 0.4
  echo worker_started
  head -1 /tmp/vca_compute/worker.log
fi
ls /dev/vcablk1 >/dev/null
EOF
done

echo '=== six devices overlapped ==='
python3 /tmp/vca_compute/sixdev.py
