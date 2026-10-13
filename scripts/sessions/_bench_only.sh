#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
$SSH root@172.31.1.1 'bash -s' <<'EOF'
if [ -f /tmp/vca_compute/worker.pid ]; then
  old=$(cat /tmp/vca_compute/worker.pid)
  kill "$old" 2>/dev/null || true
  sleep 0.3
fi
python3 /tmp/vca_compute/mbox_worker.py >/tmp/vca_compute/worker.log 2>&1 &
echo $! > /tmp/vca_compute/worker.pid
sleep 0.5
echo worker_pid=$(cat /tmp/vca_compute/worker.pid)
head -1 /tmp/vca_compute/worker.log
EOF
python3 /tmp/vca_compute/mbox_bench.py
echo '--- worker after ---'
$SSH root@172.31.1.1 'cat /tmp/vca_compute/worker.log | tail -20'
