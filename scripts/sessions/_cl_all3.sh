#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "==== $ip ===="
  $SSH root@$ip '/tmp/vca_compute/cl_bench 8388608 20'
done
