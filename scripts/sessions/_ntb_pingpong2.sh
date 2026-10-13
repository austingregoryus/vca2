#!/bin/bash
# SPDX-License-Identifier: MIT
# Continue ping-pong; guest LUT already loaded at 0x8000000 from prior insmod.
# No mmap.flush (EINVAL on this mapping). Always rmmod at the end.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
PEER=8000000

echo "=== host write ping seq=42 (no flush) ==="
python3 - <<'PY'
import os, mmap, struct
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host before magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
struct.pack_into("<IIII", m, 0, 0x414c5554, 42, 1, 0)
print("host wrote seq=42 cmd=1 st=0")
# re-read
magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
print("host reread magic=0x%x seq=%u cmd=%u st=%u" % (magic, seq, cmd, st))
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
if magic == 0x414c5554:
    struct.pack_into('<I', m, 12, 2)
    print('guest wrote status=2')
    magic, seq, cmd, st = struct.unpack_from('<IIII', m, 0)
    print('guest reread magic=0x%x seq=%u cmd=%u st=%u' % (magic, seq, cmd, st))
else:
    print('guest NO_MAGIC')
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
if st==2:
    print("PINGPONG_OK")
else:
    print("PINGPONG_FAIL")
m.close(); os.close(fd)
PY

echo "=== cleanup guest LUT ==="
$SSH root@172.31.1.1 'rmmod alut_gmap; dmesg | grep alut_gmap | tail -3; echo lsmod; lsmod | grep alut || echo no_alut_gmap'
echo "=== ping ==="
ping -c 2 -W 1 172.31.1.1
echo PINGPONG2_DONE
