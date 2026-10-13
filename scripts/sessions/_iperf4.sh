#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'iptables -I INPUT 1 -p tcp --dport 5201 -j ACCEPT; pkill iperf3 >/dev/null 2>&1; iperf3 -s -4 -D -p 5201'
done
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201
sleep 1

echo '=== host->node0 ==='
iperf3 -c 172.31.1.1 -4 -t 5 -f m
echo '=== host->node1 ==='
iperf3 -c 172.31.2.1 -4 -t 5 -f m
echo '=== node0->node1 ==='
$SSH root@172.31.1.1 'iperf3 -c 172.31.2.1 -4 -t 5 -f m'
echo '=== node0->node2 ==='
$SSH root@172.31.1.1 'iperf3 -c 172.31.3.1 -4 -t 5 -f m'
echo '=== node1->node2 ==='
$SSH root@172.31.2.1 'iperf3 -c 172.31.3.1 -4 -t 5 -f m'

echo '=== remove 5201 holes ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'iptables -D INPUT -p tcp --dport 5201 -j ACCEPT; pkill iperf3' || true
done
pkill iperf3 || true
echo done
