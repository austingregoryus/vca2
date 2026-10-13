#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== host mtu ==='
cat /sys/class/net/eth0/mtu /sys/class/net/eth1/mtu /sys/class/net/eth2/mtu

echo '=== node iptables INPUT ==='
$SSH root@172.31.1.1 'iptables -L INPUT -n | head -15; echo --- ss ---; ss -lntp | grep 5201 || true'

echo '=== start servers with log ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'pkill iperf3 >/dev/null 2>&1; iperf3 -s -D -p 5201; sleep 0.2; ss -lntp | grep 5201 || echo FAIL_LISTEN'
done
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -D -p 5201
ss -lntp | grep 5201 || echo HOST_FAIL_LISTEN

echo '=== host -> node0 ==='
iperf3 -c 172.31.1.1 -p 5201 -t 5 -f m || true
echo '=== host -> node1 ==='
iperf3 -c 172.31.2.1 -p 5201 -t 5 -f m || true
echo '=== node0 -> node1 ==='
$SSH root@172.31.1.1 'iperf3 -c 172.31.2.1 -p 5201 -t 5 -f m' || true
echo '=== ping node0->node1 ==='
$SSH root@172.31.1.1 'ping -c 2 -W 1 172.31.2.1' || true

echo '=== opencl/va on node0 ==='
$SSH root@172.31.1.1 'echo ICD; cat /etc/OpenCL/vendors/intel.icd 2>/dev/null; ls /etc/OpenCL/vendors; echo --- clinfo ---; LIBVA_DRIVERS_PATH=/opt/intel/mediasdk/lib64 LIBVA_DRIVER_NAME=iHD vainfo 2>&1 | head -30
command -v clinfo
ls /opt/intel/common/mdf/lib64
ls /opt/intel/mediasdk/lib64
# try sample_decode help
ls /opt/intel/mediasdk/samples/_bin/x64 | head
echo --- env ---
ls /opt/intel/mediasdk/lib64/iHD_drv_video.so
'

echo '=== vop miscdevs on host ==='
ls -l /dev/vop* /dev/vca* 2>/dev/null

echo '=== cleanup ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do $SSH root@$ip 'pkill iperf3' || true; done
pkill iperf3 || true
