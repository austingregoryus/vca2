#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

# generate 30 frames 320x240 NV12 (~3.3 MiB) and try Quick Sync encode
$SSH root@172.31.1.1 'bash -s' <<'EOF'
set -e
export LIBVA_DRIVER_NAME=iHD
export LIBVA_DRIVERS_PATH=/opt/intel/mediasdk/lib64
export LD_LIBRARY_PATH=/opt/intel/mediasdk/lib64:/opt/intel/mediasdk/lib/lin_x64:${LD_LIBRARY_PATH:-}
W=320; H=240; N=30
FS=$((W*H*3/2))
dd if=/dev/urandom of=/tmp/in.yuv bs=$FS count=$N status=none
echo "input bytes=$(stat -c%s /tmp/in.yuv)"
echo "--- sample_encode h264 ---"
/opt/intel/mediasdk/samples/_bin/x64/sample_encode h264 -nv12 -w $W -h $H -n $N \
  -u veryfast -i /tmp/in.yuv -o /tmp/out.h264
echo "output bytes=$(stat -c%s /tmp/out.h264 2>/dev/null || echo 0)"
file /tmp/out.h264 2>/dev/null || ls -l /tmp/out.h264
echo "--- i915 freq after encode ---"
cat /sys/kernel/debug/dri/0/i915_frequency_info | egrep "Current freq|RP0|CAGF"
echo "--- vainfo profiles (short) ---"
vainfo 2>/dev/null | egrep "Profile" | head
EOF
