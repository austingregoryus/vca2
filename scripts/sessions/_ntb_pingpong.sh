#!/bin/bash
# SPDX-License-Identifier: MIT
# Host RAM mailbox <-> guest BAR2 ping-pong. Guest LUT held only for the test.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

PEER=$($SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 host_dma=0x4c000000 host_sz=0x2000000
dmesg | grep "alut_gmap: LUT" | tail -1
' | tee /dev/stderr | sed -n "s/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p")
echo "GUEST_PEER_OFF=$PEER"
if [ -z "$PEER" ]; then echo NO_PEER; exit 1; fi
echo "=== host write ping seq=42 ==="
python3 - <<'PY'
import os, mmap, struct, time
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host before magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
struct.pack_into("<IIII", m, 0, 0x414c5554, 42, 1, 0)
m.flush()
print("host wrote seq=42 cmd=1 st=0")
m.close(); os.close(fd)
PY

echo "=== guest read + ack ==="
$SSH root@172.31.1.1 "python3 - <<'PY'
import os, mmap, struct
off=0x$PEER
path='/sys/bus/pci/devices/0000:01:00.0/resource2'
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
magic, seq, cmd, st = struct.unpack_from('<IIII', m, 0)
print('guest saw magic=0x%x seq=%u cmd=%u st=%u' % (magic, seq, cmd, st))
if magic == 0x414c5554 and cmd == 1:
    struct.pack_into('<I', m, 12, 2)
    m.flush()
    print('guest acked status=2')
else:
    print('guest NO_PING')
m.close(); os.close(fd)
PY"

echo "=== host read status ==="
python3 - <<'PY'
import os, mmap, struct
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host after magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
if seq==42 and st==2:
    print("PINGPONG_OK")
else:
    print("PINGPONG_FAIL")
m.close(); os.close(fd)
PY

echo "=== cleanup guest LUT ==="
$SSH root@172.31.1.1 'rmmod alut_gmap; dmesg | grep alut_gmap | tail -2'
echo "=== ping ==="
ping -c 2 -W 1 172.31.1.1
echo PINGPONG_HOST_DONE
