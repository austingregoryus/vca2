#!/bin/bash
# SPDX-License-Identifier: MIT
# Node 0: guest dma_alloc 32 MiB; host A-LUT maps that PA; host BAR2 push.
# Do not write host BAR2 at 0x22000000 (guest kernel RAM).
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

HOSTDIR=/tmp/vca_compute/alut_hmap
mkdir -p $HOSTDIR
cp /tmp/vca_compute/alut_hmap.c $HOSTDIR/alut_hmap.c
cp /tmp/vca_compute/Makefile.host $HOSTDIR/Makefile
echo "=== build host alut_hmap ==="
( cd $HOSTDIR && make )

echo "=== rebuild guest alut_gmap ==="
$SCP /tmp/vca_compute/alut_gmap.c root@172.31.1.1:/tmp/alut_gmap/alut_gmap.c
$SSH root@172.31.1.1 'set -e; cd /tmp/alut_gmap; make'

echo "=== guest do_alloc ==="
GLINE=$($SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_alloc=1 pci_did=0x2958 host_sz=0x400000
dmesg | grep "alut_gmap: gbuf" | tail -1
')
echo "$GLINE"
GDMA=$(echo "$GLINE" | sed -n 's/.*dma=0x\([0-9a-fA-F]*\).*/\1/p')
echo "GUEST_DMA=$GDMA"
if [ -z "$GDMA" ]; then echo NO_GDMA; exit 1; fi

echo "=== host map guest dma ==="
rmmod alut_hmap 2>/dev/null || true
insmod $HOSTDIR/alut_hmap.ko pci_did=0x2954 guest_dma=0x$GDMA size=0x400000
HLINE=$(dmesg | grep "alut_hmap: LUT" | tail -1)
echo "$HLINE"
HPEER=$(echo "$HLINE" | sed -n 's/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p')
echo "HOST_PEER=$HPEER"
if [ -z "$HPEER" ]; then echo NO_HPEER; rmmod alut_hmap || true; $SSH root@172.31.1.1 'rmmod alut_gmap'; exit 1; fi

# Refuse the known-bad window
if [ "$HPEER" = "22000000" ]; then
  echo "REFUSING host peer_off 0x22000000 (guest kernel RAM alias)"
  rmmod alut_hmap
  $SSH root@172.31.1.1 'rmmod alut_gmap'
  exit 1
fi

echo "=== host BAR2 write seq=201 ==="
python3 - <<PY
import os, mmap, struct
off=0x$HPEER
path="/sys/bus/pci/devices/0000:05:00.0/resource2"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host bar2 before magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
struct.pack_into("<IIII", m, 0, 0x414c5554, 201, 1, 0)
print("host bar2 wrote seq=201")
m.close(); os.close(fd)
PY

echo "=== guest /dev/vca_gbuf read + ack ==="
$SSH root@172.31.1.1 'python3 - <<'"'"'PY'"'"'
import os, mmap, struct
path="/dev/vca_gbuf"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("guest gbuf magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
if magic==0x414c5554 and seq==201 and cmd==1:
    struct.pack_into("<I", m, 12, 2)
    print("guest gbuf acked")
else:
    print("guest gbuf NO_PING")
m.close(); os.close(fd)
PY'

echo "=== host BAR2 read status ==="
python3 - <<PY
import os, mmap, struct, time
off=0x$HPEER
path="/sys/bus/pci/devices/0000:05:00.0/resource2"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host bar2 after magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
print("HOSTPUSH_OK" if seq==201 and st==2 else "HOSTPUSH_FAIL")
m.close(); os.close(fd)
PY

echo "=== host-push 4 MiB bandwidth (guest MAX_ORDER 4 MiB) ==="
python3 - <<PY
import os, mmap, time
off=0x$HPEER
path="/sys/bus/pci/devices/0000:05:00.0/resource2"
n=4<<20
pat=b"\x3c"*n
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, n, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
t0=time.time()
m[0:n]=pat
t1=time.time()
dt=max(t1-t0, 1e-9)
print("host_bar2_write 4MiB dt=%.6f s %.1f MiB/s" % (dt, n/dt/(1<<20)))
m.close(); os.close(fd)
PY
$SSH root@172.31.1.1 'python3 - <<'"'"'PY'"'"'
import os, mmap, time, hashlib
n=4<<20
fd=os.open("/dev/vca_gbuf", os.O_RDWR)
m=mmap.mmap(fd, n, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
t0=time.time()
data=m[0:n]
t1=time.time()
dt=max(t1-t0, 1e-9)
print("guest_gbuf_read 4MiB dt=%.6f s %.1f MiB/s first=%s md5=%s" % (
    dt, n/dt/(1<<20), data[:4].hex(), hashlib.md5(data).hexdigest()[:8]))
print("push_visible", data[:4]==b"\x3c"*4)
m.close(); os.close(fd)
PY'

echo "=== cleanup ==="
rmmod alut_hmap
$SSH root@172.31.1.1 'rmmod alut_gmap'
ping -c 2 -W 1 172.31.1.1
vcactl --skip-modprobe-check status | head -5
echo PUSH_DONE
