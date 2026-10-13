#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'

logtemp() { echo "T $(date +%H:%M:%S)"; vcactl temp 2>/dev/null | grep -E 'Card|Core'; }

echo '=== temps BEFORE ==='
logtemp

echo '=== build/start daemons ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP /tmp/vca_compute/cl_daemon.c root@$ip:/tmp/vca_compute/cl_daemon.c
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
gcc -O2 -o /tmp/vca_compute/cl_daemon /tmp/vca_compute/cl_daemon.c -ldl
if [ -f /tmp/vca_compute/daemon.pid ]; then
  kill "$(cat /tmp/vca_compute/daemon.pid)" 2>/dev/null || true
  sleep 0.2
fi
iptables -C INPUT -p tcp --dport 9200 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 9200 -j ACCEPT
/tmp/vca_compute/cl_daemon >/tmp/vca_compute/daemon.log 2>&1 &
echo $! > /tmp/vca_compute/daemon.pid
sleep 0.8
head -5 /tmp/vca_compute/daemon.log
EOF
done

echo '=== temps after daemon start ==='
logtemp

echo '=== warm parallel SAXPY ==='
python3 /tmp/vca_compute/gpu_warm.py

echo '=== temps after warm SAXPY ==='
logtemp

echo '=== start temp logger + 12s GPU burn all 3 ==='
( while true; do
    echo "---- $(date -Is) ----"
    vcactl temp 2>/dev/null || true
    sleep 2
  done ) >/tmp/vca_temp_burn.log 2>&1 &
LOGGER=$!

python3 - <<'PY'
import socket, threading, time
NODES = [("n0","172.31.1.1"),("n1","172.31.2.1"),("n2","172.31.3.1")]
N=8388608; ITERS=20; SECS=12
out={}
def one(label, ip, base):
    s=socket.create_connection((ip,9200), timeout=5)
    s.settimeout(SECS+30)
    f=s.makefile("rw")
    hello=f.readline()
    t0=time.time()
    f.write("BURN %d %d %d %d\n" % (N, ITERS, base, SECS))
    f.flush()
    resp=f.readline()
    dt=time.time()-t0
    f.write("QUIT\n"); f.flush(); s.close()
    out[label]=(dt, hello.strip(), resp.strip())
ths=[]
t0=time.time()
for i,(l,ip) in enumerate(NODES):
    th=threading.Thread(target=one, args=(l,ip,i*N)); th.start(); ths.append(th)
for th in ths: th.join()
print("burn_wall=%.3fs" % (time.time()-t0))
for l,_ in NODES:
    print(l, out[l][0], out[l][2][:200])
PY

kill $LOGGER 2>/dev/null || true
sleep 1
echo '=== temps AFTER burn ==='
logtemp
echo '=== temp log during burn ==='
cat /tmp/vca_temp_burn.log

echo '=== hottest core during burn ==='
python3 - <<'PY'
import re
text=open("/tmp/vca_temp_burn.log").read()
vals=[int(x) for x in re.findall(r"\+(\d+)\.0 C", text)]
print("samples=%d max=%s min=%s" % (len(vals), max(vals) if vals else None, min(vals) if vals else None))
if vals and max(vals) >= 85:
    print("THERMAL_HOT")
else:
    print("THERMAL_OK")
PY
