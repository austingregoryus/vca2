#!/bin/bash
# SPDX-License-Identifier: MIT
# Node 0 only: raise MaxReadReq, measure iperf3 + ALUT guest pull.
# Restore DevCtl at the end. Do not touch nodes 1/2. Do not cat DMA debugfs.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

mrr_enc() { # 0=128 .. 4=2048
  case $1 in
    128) echo 0 ;; 256) echo 1 ;; 512) echo 2 ;; 1024) echo 3 ;; 2048) echo 4 ;;
    *) echo BAD; return 1 ;;
  esac
}
devctl_show() {
  local tag=$1 bdf=$2
  local w
  w=$(setpci -s "$bdf" CAP_EXP+8.w 2>/dev/null || echo missing)
  echo "$tag $bdf DEVCTL=$w"
  lspci -s "$bdf" -vv 2>/dev/null | grep -E "MaxReadReq|MaxPayload|LnkSta:"
}

echo "=== health ==="
ping -c 1 -W 1 172.31.1.1
vcactl --skip-modprobe-check status | head -5

echo "=== baseline lspci ==="
devctl_show HOST 0000:05:00.0
$SSH root@172.31.1.1 'setpci -s 0000:01:00.0 CAP_EXP+8.w; lspci -s 01:00.0 -vv | grep -E "MaxReadReq|MaxPayload|LnkSta:"'
HOST_W=$(setpci -s 0000:05:00.0 CAP_EXP+8.w)
GUEST_W=$($SSH root@172.31.1.1 'setpci -s 0000:01:00.0 CAP_EXP+8.w')
echo "SAVE host=$HOST_W guest=$GUEST_W"

set_mrr_host() {
  local enc=$1
  local w nw
  w=$(setpci -s 0000:05:00.0 CAP_EXP+8.w)
  nw=$(printf '%04x' $(( (0x$w & ~0x7000) | (enc << 12) )))
  setpci -s 0000:05:00.0 CAP_EXP+8.w=$nw
  echo "host DEVCTL $w -> $nw"
}
set_mrr_guest() {
  local enc=$1
  $SSH root@172.31.1.1 "w=\$(setpci -s 0000:01:00.0 CAP_EXP+8.w); nw=\$(printf '%04x' \$(( (0x\$w & ~0x7000) | ($enc << 12) ))); setpci -s 0000:01:00.0 CAP_EXP+8.w=\$nw; echo guest DEVCTL \$w -> \$nw"
}

restore() {
  echo "=== restore DevCtl ==="
  setpci -s 0000:05:00.0 CAP_EXP+8.w=$HOST_W || true
  $SSH root@172.31.1.1 "setpci -s 0000:01:00.0 CAP_EXP+8.w=$GUEST_W" || true
  ping -c 1 -W 2 172.31.1.1 || true
}

alut_pull() {
  local tag=$1
  echo "=== ALUT pull $tag ==="
  $SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 pci_did=0x2958 host_dma=0x4c000000 host_sz=0x2000000
dmesg | grep "alut_gmap: LUT" | tail -1
'
  local line peer
  line=$($SSH root@172.31.1.1 'dmesg | grep "alut_gmap: LUT" | tail -1')
  peer=$(echo "$line" | sed -n 's/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p')
  echo "$line"
  if [ -z "$peer" ]; then echo NO_PEER; return 1; fi
  python3 - <<PY
import os, mmap
path="/dev/vca_alut_0000_05_00_0"
n=8<<20
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, n, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
m[0:n]=b"\\x5a"*n
m.close(); os.close(fd)
print("host filled 8MiB")
PY
  $SSH root@172.31.1.1 "python3 - <<'PY'
import os, mmap, time
off=0x$peer
path='/sys/bus/pci/devices/0000:01:00.0/resource2'
fd=os.open(path, os.O_RDONLY)
m=mmap.mmap(fd, 8<<20, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
for n in (1<<20, 8<<20):
    t0=time.time()
    data=m[0:n]
    dt=max(time.time()-t0, 1e-9)
    print('ALUT_PULL $tag n=%u dt=%.4f s %.1f MiB/s first=%s' % (n, dt, n/dt/(1<<20), data[:4].hex()))
m.close(); os.close(fd)
PY"
  $SSH root@172.31.1.1 'rmmod alut_gmap'
  ping -c 1 -W 1 172.31.1.1
}

echo "=== iperf prep ==="
$SSH root@172.31.1.1 'iptables -C INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 5201 -j ACCEPT
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201'
pkill iperf3 >/dev/null 2>&1 || true
iperf3 -s -4 -D -p 5201
sleep 0.5

iperf_pair() {
  local tag=$1
  echo "=== iperf host->n0 $tag ==="
  iperf3 -c 172.31.1.1 -4 -t 5 -f m
  echo "=== iperf n0->host $tag ==="
  $SSH root@172.31.1.1 'iperf3 -c 172.31.1.254 -4 -t 5 -f m'
  ping -c 1 -W 1 172.31.1.1
}

alut_pull baseline
iperf_pair baseline

echo "=== set MRR 512 both ends node0 ==="
set_mrr_host 2
set_mrr_guest 2
devctl_show HOST 0000:05:00.0
$SSH root@172.31.1.1 'lspci -s 01:00.0 -vv | grep -E "MaxReadReq|MaxPayload"'
if ! ping -c 2 -W 1 172.31.1.1; then echo PING_FAIL_512; restore; exit 1; fi
alut_pull mrr512
iperf_pair mrr512

echo "=== set MRR 2048 both ends node0 ==="
set_mrr_host 4
set_mrr_guest 4
devctl_show HOST 0000:05:00.0
$SSH root@172.31.1.1 'lspci -s 01:00.0 -vv | grep -E "MaxReadReq|MaxPayload"'
if ! ping -c 2 -W 1 172.31.1.1; then echo PING_FAIL_2048; restore; exit 1; fi
alut_pull mrr2048
iperf_pair mrr2048

restore
devctl_show HOST 0000:05:00.0
$SSH root@172.31.1.1 'lspci -s 01:00.0 -vv | grep -E "MaxReadReq|MaxPayload"'

echo "=== cleanup iperf ==="
$SSH root@172.31.1.1 'iptables -D INPUT -p tcp --dport 5201 -j ACCEPT 2>/dev/null || true; pkill iperf3' || true
pkill iperf3 || true
vcactl --skip-modprobe-check status | head -5
ping -c 1 -W 1 172.31.1.1
ping -c 1 -W 1 172.31.2.1
echo MRR_DONE
