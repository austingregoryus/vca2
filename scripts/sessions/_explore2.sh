#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== SAFE debugfs: spad + alm_state node0 host ==='
echo '--- spad 05:00.0 ---'
cat /sys/kernel/debug/plx87xx/0000:05:00.0/spad
echo '--- msi_irq_info ---'
cat /sys/kernel/debug/plx87xx/0000:05:00.0/msi_irq_info
echo '--- alm_state (first 40 lines) ---'
head -40 /sys/kernel/debug/plx87xx/0000:05:00.0/alm_state
echo '--- alm mapped count ---'
grep -c 'ref_cnt:[1-9]' /sys/kernel/debug/plx87xx/0000:05:00.0/alm_state || true
echo '--- alm header ---'
head -1 /sys/kernel/debug/plx87xx/0000:05:00.0/alm_state

echo
echo '=== ensure worker on node0 ==='
$SSH root@172.31.1.1 'if [ -f /tmp/vca_compute/worker.pid ] && kill -0 $(cat /tmp/vca_compute/worker.pid) 2>/dev/null; then echo worker_alive pid=$(cat /tmp/vca_compute/worker.pid); else python3 /tmp/vca_compute/mbox_worker.py >/tmp/vca_compute/worker.log 2>&1 & echo $! > /tmp/vca_compute/worker.pid; sleep 0.5; cat /tmp/vca_compute/worker.log; fi'

echo
echo '=== mailbox size sweep ==='
python3 /tmp/vca_compute/mbox_bench.py

echo
echo '=== node0 GPU compile env ==='
$SSH root@172.31.1.1 'command -v gcc; command -v g++; ls /usr/include/CL 2>/dev/null; ls /opt/intel/mediasdk/include | head; find /opt/intel /usr/local -name "cl.h" 2>/dev/null | head; ls /opt/intel/mediasdk/samples/_bin/x64 | head -20; /opt/intel/mediasdk/samples/_bin/x64/sample_encode --help 2>&1 | head -25'

echo
echo '=== i915 freq now ==='
$SSH root@172.31.1.1 'cat /sys/kernel/debug/dri/0/i915_frequency_info | egrep "Current|Max|RP0|CAGF|RPN"'
