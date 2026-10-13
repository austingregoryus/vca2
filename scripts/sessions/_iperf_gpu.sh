#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== MTU ==='
ip -br link | grep -E 'eth[012]'
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo -n "$ip "; $SSH root@$ip 'cat /sys/class/net/eth0/mtu; ethtool eth0 2>/dev/null | grep -i speed || true'
done

echo
echo '=== start iperf3 servers ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'pkill iperf3 >/dev/null 2>&1; iperf3 -s -D -p 5201'
done
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -D -p 5201 || true
sleep 1

run_tcp() {
  local src=$1 dst=$2 label=$3
  echo "----- TCP $label -----"
  if [ "$src" = host ]; then
    iperf3 -c "$dst" -p 5201 -t 6 -f m
  else
    $SSH root@$src "iperf3 -c $dst -p 5201 -t 6 -f m"
  fi
}

echo
echo '=== host -> nodes ==='
run_tcp host 172.31.1.1 'host->node0'
run_tcp host 172.31.2.1 'host->node1'
run_tcp host 172.31.3.1 'host->node2'

echo
echo '=== nodes -> host ==='
run_tcp 172.31.1.1 172.31.1.254 'node0->host'
run_tcp 172.31.2.1 172.31.2.254 'node1->host'
run_tcp 172.31.3.1 172.31.3.254 'node2->host'

echo
echo '=== node <-> node (via host router) ==='
run_tcp 172.31.1.1 172.31.2.1 'node0->node1'
run_tcp 172.31.1.1 172.31.3.1 'node0->node2'
run_tcp 172.31.2.1 172.31.3.1 'node1->node2'

echo
echo '=== GPU / media on node0 ==='
$SSH root@172.31.1.1 'echo --- drm ---; cat /sys/class/drm/card0/device/uevent; echo --- i915 freq ---; cat /sys/kernel/debug/dri/0/i915_frequency_info 2>/dev/null | head -25 || echo no debugfs i915; echo --- vainfo ---; vainfo 2>&1 | head -25; echo --- i965 so ---; ls /usr/lib/x86_64-linux-gnu/dri /usr/lib64/dri 2>/dev/null; echo --- msdk ---; ls /opt/intel/mediasdk/lib64 | head; ls /opt/intel/mediasdk/lib/lin_x64 | head; echo --- samples ---; ls /opt/intel/mediasdk/samples/_bin 2>/dev/null | head; echo --- opencl ---; find /opt/intel /usr -name "*OpenCL*" -o -name "*beignet*" -o -name "intel.icd" 2>/dev/null | head -30; ls /etc/OpenCL/vendors 2>/dev/null; echo --- render ---; ls -l /dev/dri'

echo
echo '=== stop iperf3 servers ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'pkill iperf3 >/dev/null 2>&1' || true
done
pkill iperf3 >/dev/null 2>&1 || true
echo done
