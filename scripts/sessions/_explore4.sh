#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

echo '=== guest NTB MaxPayload / MaxReadReq ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "-- $ip --"
  $SSH root@$ip 'lspci -d 8086:2958 -vv 2>/dev/null | egrep "LnkSta:|MaxPayload|MaxReadReq|Width"; lspci -d 8086:2959 -vv 2>/dev/null | egrep "LnkSta:|MaxPayload|MaxReadReq"; lspci -d 8086:295a -vv 2>/dev/null | egrep "LnkSta:|MaxPayload|MaxReadReq"'
done

echo
echo '=== host NTB link ==='
for bdf in 05:00.0 07:00.0 0a:00.0; do
  echo "-- $bdf --"
  lspci -s $bdf -vv 2>/dev/null | egrep "LnkSta:|MaxPayload|MaxReadReq"
done

echo
echo '=== OpenCL on nodes 1 and 2 ==='
for ip in 172.31.2.1 172.31.3.1; do
  echo "==== $ip ===="
  $SCP /tmp/vca_compute/cl_probe.c /tmp/vca_compute/cl_bench.c root@$ip:/tmp/vca_compute/ 2>/dev/null || {
    $SSH root@$ip 'mkdir -p /tmp/vca_compute'
    $SCP /tmp/vca_compute/cl_probe.c /tmp/vca_compute/cl_bench.c root@$ip:/tmp/vca_compute/
  }
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
mkdir -p /tmp/vca_compute
if ! ldconfig -p | grep -q libOpenCL.so; then
  apt-get install -y ocl-icd-libopencl1 >/dev/null
fi
gcc -O2 -o /tmp/vca_compute/cl_probe /tmp/vca_compute/cl_probe.c -ldl
gcc -O2 -o /tmp/vca_compute/cl_bench /tmp/vca_compute/cl_bench.c -ldl
/tmp/vca_compute/cl_probe
EOF
done

echo
echo '=== node0 GPU vs CPU SAXPY 8M x 20 ==='
$SCP /tmp/vca_compute/cl_bench.c root@172.31.1.1:/tmp/vca_compute/cl_bench.c
$SSH root@172.31.1.1 'gcc -O2 -o /tmp/vca_compute/cl_bench /tmp/vca_compute/cl_bench.c -ldl && /tmp/vca_compute/cl_bench 8388608 20; echo --- freq ---; cat /sys/kernel/debug/dri/0/i915_frequency_info | egrep "Current freq|CAGF"'
