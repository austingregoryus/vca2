#!/bin/bash
# SPDX-License-Identifier: MIT
# Node 0 jumbo: lift host eth0 max_mtu live, match guest MTU, ping -M do, iperf3.
# Restores host MTU on rmmod. Does not rmmod plx87xx. Nodes 1/2 untouched.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo "=== ifaces ==="
ip -o addr show | grep -E "eth0|172.31.1"
$SSH root@172.31.1.1 'ip -o link show; ip -o addr show | head -10; cat /sys/class/net/*/mtu 2>/dev/null | head'
echo "host eth0 mtu=$(cat /sys/class/net/eth0/mtu)"
ls /sys/class/net/eth0/max_mtu 2>/dev/null || echo "no sysfs max_mtu"

echo "=== ping DF 1472 (should pass) ==="
ping -c 1 -W 1 -s 1472 -M do 172.31.1.1 || true
echo "=== ping DF 2000 (should fail at 1500) ==="
ping -c 1 -W 1 -s 2000 -M do 172.31.1.1 || true

echo "=== build jumbo_mtu ==="
mkdir -p /tmp/vca_compute/jumbo
cp /tmp/vca_compute/jumbo_mtu.c /tmp/vca_compute/jumbo/jumbo_mtu.c
cp /tmp/vca_compute/jumbo_Makefile /tmp/vca_compute/jumbo/Makefile
( cd /tmp/vca_compute/jumbo && make )

echo "=== insmod mtu=9000 ==="
rmmod jumbo_mtu 2>/dev/null || true
insmod /tmp/vca_compute/jumbo/jumbo_mtu.ko ifname=eth0 mtu=9000
dmesg | grep jumbo_mtu | tail -3
ip link show eth0
GUEST_IF=$($SSH root@172.31.1.1 "ip -o addr show to 172.31.1.1 | awk '{print \$2}' | sed 's/://'")
echo "GUEST_IF=$GUEST_IF"
G_OLD=$($SSH root@172.31.1.1 "cat /sys/class/net/$GUEST_IF/mtu")
echo "guest old mtu=$G_OLD"
$SSH root@172.31.1.1 "ip link set $GUEST_IF mtu 9000; cat /sys/class/net/$GUEST_IF/mtu"

echo "=== ping DF 8972 at MTU 9000 ==="
ping -c 3 -W 1 -s 8972 -M do 172.31.1.1 || echo "JUMBO_PING_FAIL"

echo "=== iperf 9000 ==="
$SSH root@172.31.1.1 'iptables -C INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 5201 -j ACCEPT
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201'
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201
sleep 0.4
echo "--- host->n0 ---"
iperf3 -c 172.31.1.1 -4 -t 5 -f m
echo "--- n0->host ---"
$SSH root@172.31.1.1 'iperf3 -c 172.31.1.254 -4 -t 5 -f m'
ping -c 1 -W 1 172.31.1.1

echo "=== try host+guest 65521 ==="
rtnl_ok=1
if insmod /tmp/vca_compute/jumbo/jumbo_mtu.ko ifname=eth0 mtu=65521 2>/tmp/jumbo65521.err; then
  echo "second insmod unexpected"
else
  echo "need rmmod first to change mtu via module; set via ip"
  ip link set eth0 mtu 65521 && echo "host 65521 OK" || echo "host 65521 FAIL"
fi
$SSH root@172.31.1.1 "ip link set $GUEST_IF mtu 65521; cat /sys/class/net/$GUEST_IF/mtu"
ip link show eth0 | head -2
echo "=== ping DF 20000 (if mtu allows) ==="
ping -c 1 -W 1 -s 20000 -M do 172.31.1.1 || echo "PING_20k_FAIL"
HMTU=$(cat /sys/class/net/eth0/mtu)
if [ "$HMTU" -ge 65521 ]; then
  echo "=== iperf 65521 ==="
  iperf3 -c 172.31.1.1 -4 -t 5 -f m
  $SSH root@172.31.1.1 'iperf3 -c 172.31.1.254 -4 -t 5 -f m'
fi

echo "=== restore ==="
$SSH root@172.31.1.1 "ip link set $GUEST_IF mtu $G_OLD; iptables -D INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || true; pkill iperf3" || true
pkill iperf3 || true
rmmod jumbo_mtu
ip link show eth0 | head -2
ping -c 1 -W 1 -s 1472 -M do 172.31.1.1
ping -c 1 -W 1 172.31.1.1
ping -c 1 -W 1 172.31.2.1
vcactl --skip-modprobe-check status | head -5
echo JUMBO_DONE
