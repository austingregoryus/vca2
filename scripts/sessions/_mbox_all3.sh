#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

for cpu in 1 2; do
  f=/tank-red/vmdata/vca/mbox${cpu}.bin
  if [[ ! -f "$f" ]]; then
    dd if=/dev/zero of="$f" bs=1M count=16 status=none
  fi
  if ! vcactl blockio list 0 "$cpu" | grep -q vcablk1; then
    echo "open vcablk1 on cpu $cpu"
    vcactl blockio open 0 "$cpu" vcablk1 RW "$f"
  fi
done
vcactl blockio list

nodes=(172.31.1.1 172.31.2.1 172.31.3.1)
for ip in "${nodes[@]}"; do
  $SSH root@$ip 'mkdir -p /tmp/vca_compute'
  $SCP /tmp/vca_compute/mbox_proto.py /tmp/vca_compute/mbox_worker.py root@$ip:/tmp/vca_compute/
  $SSH root@$ip 'bash -s' <<'EOF'
mkdir -p /tmp/vca_compute
if [ -f /tmp/vca_compute/worker.pid ]; then
  kill "$(cat /tmp/vca_compute/worker.pid)" 2>/dev/null || true
  sleep 0.2
fi
python3 /tmp/vca_compute/mbox_worker.py >/tmp/vca_compute/worker.log 2>&1 &
echo $! > /tmp/vca_compute/worker.pid
sleep 0.4
head -1 /tmp/vca_compute/worker.log
ls /dev/vcablk1
EOF
done

python3 /tmp/vca_compute/mbox_host3.py
