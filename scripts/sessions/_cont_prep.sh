#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

echo '=== host iperf3 ==='
if ! command -v iperf3 >/dev/null; then
  apt-get update -qq >/dev/null 2>&1 || true
  apt-get install -y iperf3 >/dev/null 2>&1 || echo 'host apt iperf3 failed'
fi
command -v iperf3 || true

install_iperf_on_node() {
  local ip=$1
  $SSH root@$ip 'command -v iperf3 && exit 0
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y iperf3 >/dev/null 2>&1
    command -v iperf3 || echo NO_IPERF'
}

echo '=== node iperf3 ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "-- $ip --"
  install_iperf_on_node $ip
done
