#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== iptables INPUT verbose node0 ==='
$SSH root@172.31.1.1 'iptables -L INPUT -n -v | head -20'

echo '=== host max mtu probe ==='
for m in 1500 2048 4096 8192 9000 16384 32000 65521; do
  if ip link set eth0 mtu $m 2>/dev/null; then echo "eth0 mtu $m OK"; else echo "eth0 mtu $m FAIL"; break; fi
done
ip link set eth0 mtu 1500 2>/dev/null || true
cat /sys/class/net/eth0/mtu

echo '=== iperf3 -4 servers ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'pkill iperf3 >/dev/null 2>&1; iperf3 -s -4 -D -p 5201; ss -lntp | grep 5201'
done
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201

echo '=== host->node0 ==='
iperf3 -c 172.31.1.1 -4 -p 5201 -t 5 -f m
echo '=== host->node1 ==='
iperf3 -c 172.31.2.1 -4 -p 5201 -t 5 -f m
echo '=== node0->node1 ==='
$SSH root@172.31.1.1 'iperf3 -c 172.31.2.1 -4 -p 5201 -t 5 -f m'
echo '=== node0->node2 ==='
$SSH root@172.31.1.1 'iperf3 -c 172.31.3.1 -4 -p 5201 -t 5 -f m'

echo '=== opencl node0 ==='
$SSH root@172.31.1.1 'ls -l /usr/local/lib64/libigdrcl.so; ldd /usr/local/lib64/libigdrcl.so 2>/dev/null | head; ls /usr/local/bin | grep -i cl; find /opt/intel /usr/local -name clinfo 2>/dev/null; echo ---; cat /sys/class/drm/card0/device/enable; echo render group; id'

echo '=== cleanup ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do $SSH root@$ip 'pkill iperf3' || true; done
pkill iperf3 || true
ip link set eth0 mtu 1500 2>/dev/null || true
