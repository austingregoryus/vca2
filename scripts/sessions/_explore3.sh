#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

echo '=== host virtio max_mtu ==='
for n in 0 1 2; do
  echo -n "eth$n mtu=$(cat /sys/class/net/eth$n/mtu) max="
  cat /sys/class/net/eth$n/max_mtu 2>/dev/null || echo n/a
done

echo
echo '=== dmesg ALUT num_ntbs ==='
dmesg | grep -i 'num ntbs\|programmed a lut\|A-LUT array base\|Device 0x295' | tail -30

echo
echo '=== copy cl_probe to node0 and build ==='
$SSH root@172.31.1.1 'mkdir -p /tmp/vca_compute'
$SCP /tmp/vca_compute/cl_probe.c root@172.31.1.1:/tmp/vca_compute/cl_probe.c
$SSH root@172.31.1.1 'bash -s' <<'EOF'
set -e
cd /tmp/vca_compute
gcc -O2 -o cl_probe cl_probe.c -ldl
echo '--- libOpenCL / icd ---'
ls -l /usr/local/lib64/libigdrcl.so /usr/lib/*/libOpenCL* /etc/OpenCL/vendors/* 2>/dev/null || true
echo '--- run ---'
export OCL_ICD_VENDORS=/etc/OpenCL/vendors
./cl_probe; echo exit=$?
echo '--- i915 freq ---'
cat /sys/kernel/debug/dri/0/i915_frequency_info | egrep "Current freq|CAGF"
EOF
