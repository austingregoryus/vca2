#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

$SSH root@172.31.1.1 'mkdir -p /tmp/vca_compute'
$SCP /tmp/vca_compute/mbox_proto.py /tmp/vca_compute/mbox_worker.py root@172.31.1.1:/tmp/vca_compute/

$SSH root@172.31.1.1 'bash -s' <<'EOF'
set -e
mkdir -p /tmp/vca_compute
if [ -f /tmp/vca_compute/worker.pid ]; then
  kill "$(cat /tmp/vca_compute/worker.pid)" 2>/dev/null || true
fi
python3 /tmp/vca_compute/mbox_worker.py >/tmp/vca_compute/worker.log 2>&1 &
echo $! > /tmp/vca_compute/worker.pid
sleep 1
echo '--- worker log ---'
cat /tmp/vca_compute/worker.log
ls -l /dev/vcablk1
EOF

echo '=== host test ==='
python3 /tmp/vca_compute/mbox_host.py
echo '=== worker log after ==='
$SSH root@172.31.1.1 'cat /tmp/vca_compute/worker.log'
