#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'
mkdir -p /tmp/vca_compute
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_bench.c root@$ip:/tmp/vca_compute/cl_bench.c
  $SSH root@$ip 'gcc -O2 -o /tmp/vca_compute/cl_bench /tmp/vca_compute/cl_bench.c -ldl'
done
python3 /tmp/vca_compute/gpu_all3.py
