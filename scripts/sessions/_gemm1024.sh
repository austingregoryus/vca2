#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

coremax() {
  vcactl temp 2>/dev/null | awk '/Core/ && !/high/ {gsub(/[^0-9.]/,"",$2); if($2+0>m) m=$2+0} END{print m+0}'
}

echo "=== temps BEFORE max=$(coremax) ==="
vcactl temp 2>/dev/null | grep -E 'Card|Core'

echo '=== rebuild daemons ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_daemon.c root@$ip:/tmp/vca_compute/cl_daemon.c
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
gcc -O2 -o /tmp/vca_compute/cl_daemon /tmp/vca_compute/cl_daemon.c -ldl
if [ -f /tmp/vca_compute/daemon.pid ]; then
  kill "$(cat /tmp/vca_compute/daemon.pid)" 2>/dev/null || true
  sleep 0.3
fi
/tmp/vca_compute/cl_daemon >/tmp/vca_compute/daemon.log 2>&1 &
echo $! > /tmp/vca_compute/daemon.pid
sleep 0.8
cat /tmp/vca_compute/daemon.log
EOF
done

( while true; do
    echo "---- $(date -Is) ----"
    vcactl temp 2>/dev/null | grep Core | grep -v high || true
    sleep 2
  done ) >/tmp/vca_temp_gemm.log 2>&1 &
LOGGER=$!

python3 /tmp/vca_compute/gpu_gemm1024.py
rc=$?

kill $LOGGER 2>/dev/null || true
echo "=== temps AFTER max=$(coremax) ==="
vcactl temp 2>/dev/null | grep -E 'Card|Core'
echo '=== peak cores during (Core lines only) ==='
python3 - <<'PY'
import re
text=open("/tmp/vca_temp_gemm.log").read()
vals=[]
for line in text.splitlines():
    if "Core" in line and "high" not in line:
        m=re.search(r"\+(\d+)", line)
        if m: vals.append(int(m.group(1)))
print("samples", len(vals), "max", max(vals) if vals else None, "min", min(vals) if vals else None)
print("THERMAL_OK" if (not vals or max(vals)<85) else "THERMAL_HOT")
PY
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'echo pkg=$(cat /sys/class/thermal/thermal_zone2/temp) gt=$(cat /sys/class/drm/card0/gt_cur_freq_mhz)'
exit $rc
