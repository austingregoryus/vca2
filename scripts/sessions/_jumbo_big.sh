#!/bin/bash
# SPDX-License-Identifier: MIT
# Confirm operational MTU 65521 on eth0/1/2; DF ping; short iperf node1/2.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
KO=/root/vca2/jumbo_mtu.ko

echo "=== host mtu ==="
for n in 0 1 2; do echo -n "eth$n "; cat /sys/class/net/eth$n/mtu; done
if ! lsmod | grep -q '^jumbo_mtu'; then
  echo "jumbo_mtu not loaded; inserting"
  insmod "$KO" ifnames=eth0,eth1,eth2 mtu=65521
fi
# if loaded but mtu not 65521, rmmod/insmod
need=0
for n in 0 1 2; do
  m=$(cat /sys/class/net/eth$n/mtu)
  [ "$m" = 65521 ] || need=1
done
if [ "$need" = 1 ]; then
  rmmod jumbo_mtu 2>/dev/null || true
  insmod "$KO" ifnames=eth0,eth1,eth2 mtu=65521
fi
for n in 0 1 2; do echo -n "eth$n "; cat /sys/class/net/eth$n/mtu; done

echo "=== guest mtu ==="
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo -n "$ip "
  $SSH root@$ip 'cat /sys/class/net/eth0/mtu'
done

echo "=== DF ping 20000 ==="
ping -c 1 -W 1 -s 20000 -M do 172.31.1.1
ping -c 1 -W 1 -s 20000 -M do 172.31.2.1
ping -c 1 -W 1 -s 20000 -M do 172.31.3.1

echo "=== iperf 5s n1 and n2 ==="
for ip in 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'iptables -C INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 5201 -j ACCEPT
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201'
done
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201
sleep 0.4
echo "--- host->n1 ---"
iperf3 -c 172.31.2.1 -4 -t 5 -f m
echo "--- n1->host ---"
$SSH root@172.31.2.1 'iperf3 -c 172.31.2.254 -4 -t 5 -f m'
echo "--- host->n2 ---"
iperf3 -c 172.31.3.1 -4 -t 5 -f m
echo "--- n2->host ---"
$SSH root@172.31.3.1 'iperf3 -c 172.31.3.254 -4 -t 5 -f m'

echo "=== cleanup 5201 ==="
for ip in 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'iptables -D INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || true; pkill iperf3' || true
done
pkill iperf3 || true
vcactl --skip-modprobe-check status | head -5
lsmod | grep jumbo
echo JUMBO_BIG_OK
