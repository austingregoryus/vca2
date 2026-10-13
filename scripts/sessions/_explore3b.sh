#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== host eth0 sysfs mtu files ==='
ls -l /sys/class/net/eth0/ | grep -i mtu
ip link set eth0 mtu 2048; echo change2048=$?
ip link set eth0 mtu 1500

echo
echo '=== node0 OpenCL symbols + ICD loader ==='
$SSH root@172.31.1.1 'bash -s' <<'EOF'
echo '--- icd ---'
cat /etc/OpenCL/vendors/intel.icd
echo '--- nm igdrcl ---'
nm -D /usr/local/lib64/libigdrcl.so 2>/dev/null | egrep 'clGet|Init|opencl' | head -40
echo '--- ldconfig OpenCL ---'
ldconfig -p | grep -i opencl || true
echo '--- try apt ocl-icd ---'
apt-get install -y ocl-icd-libopencl1 ocl-icd-opencl-dev 2>&1 | tail -20
ldconfig -p | grep -i opencl || true
ls /usr/lib/*/libOpenCL* 2>/dev/null
echo '--- rerun cl_probe ---'
cd /tmp/vca_compute && ./cl_probe; echo exit=$?
EOF
