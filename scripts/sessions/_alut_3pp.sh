#!/bin/bash
# SPDX-License-Identifier: MIT
# Build alut_gmap on all 3 guests, map host DMA, header ping-pong, rmmod.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

SRC=/tmp/vca_compute
$SCP $SRC/alut_gmap.c $SRC/Makefile.guest root@172.31.1.1:/tmp/ 2>/dev/null || true

deploy() {
  local ip=$1
  $SSH root@$ip "mkdir -p /tmp/alut_gmap"
  $SCP $SRC/alut_gmap.c root@$ip:/tmp/alut_gmap/alut_gmap.c
  $SCP $SRC/Makefile.guest root@$ip:/tmp/alut_gmap/Makefile
  $SSH root@$ip "set -e; cd /tmp/alut_gmap; make"
}

echo "=== deploy build ==="
cp $SRC/alut_gmap.c $SRC/alut_gmap.c.bak 2>/dev/null || true
deploy 172.31.1.1
deploy 172.31.2.1
deploy 172.31.3.1

pp() {
  local ip=$1 did=$2 dma=$3 sz=$4 hostdev=$5 seq=$6
  echo "======== $ip did=$did dma=$dma sz=$sz seq=$seq ========"
  local line peer
  line=$($SSH root@$ip "set -e
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 pci_did=$did host_dma=$dma host_sz=$sz
dmesg | grep 'alut_gmap: LUT' | tail -1
")
  echo "$line"
  peer=$(echo "$line" | sed -n 's/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p')
  if [ -z "$peer" ]; then echo NO_PEER; $SSH root@$ip 'dmesg | grep alut_gmap | tail -8'; return 1; fi
  echo "GUEST_PEER=$peer"

  python3 - <<PY
import os, mmap, struct
path="$hostdev"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
struct.pack_into("<IIII", m, 0, 0x414c5554, $seq, 1, 0)
print("host wrote seq=$seq")
m.close(); os.close(fd)
PY

  $SSH root@$ip "python3 - <<'PY'
import os, mmap, struct
off=0x$peer
path='/sys/bus/pci/devices/0000:01:00.0/resource2'
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
magic, seq, cmd, st = struct.unpack_from('<IIII', m, 0)
print('guest saw magic=0x%x seq=%u cmd=%u st=%u' % (magic, seq, cmd, st))
ok = (magic == 0x414c5554 and seq == $seq and cmd == 1)
if ok:
    struct.pack_into('<I', m, 12, 2)
    print('guest acked')
else:
    print('guest NO_PING')
m.close(); os.close(fd)
PY"

  python3 - <<PY
import os, mmap, struct
path="$hostdev"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host after magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
print("PINGPONG_OK" if seq==$seq and st==2 else "PINGPONG_FAIL")
m.close(); os.close(fd)
PY

  $SSH root@$ip "rmmod alut_gmap"
  ping -c 1 -W 1 $ip
}

pp 172.31.1.1 0x2958 0x4c000000 0x2000000 /dev/vca_alut_0000_05_00_0 101
pp 172.31.2.1 0x2959 0x48000000 0x2000000 /dev/vca_alut_0000_07_00_0 102
pp 172.31.3.1 0x295a 0x47000000 0x1000000 /dev/vca_alut_0000_0a_00_0 103

echo "=== final ==="
vcactl --skip-modprobe-check status | head -5
ping -c 1 -W 1 172.31.1.1
ping -c 1 -W 1 172.31.2.1
ping -c 1 -W 1 172.31.3.1
echo ALL3_DONE
