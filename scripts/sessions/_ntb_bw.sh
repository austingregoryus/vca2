#!/bin/bash
# SPDX-License-Identifier: MIT
# 1 MiB / 8 MiB memcpy timing over the ALUT window. Guest LUT for the test only.
set -u
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

python3 - <<'PY'
import os, mmap, struct, time
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 8<<20, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
n=8<<20
m[0:n] = b"\x5a" * n
print("host filled 8MiB with 0x5a")
m.close(); os.close(fd)
PY

echo "=== guest read 1MiB and 8MiB ==="
$SSH root@172.31.1.1 "python3 - <<'PY'
import os, mmap, time, hashlib
off=0x$PEER
path='/sys/bus/pci/devices/0000:01:00.0/resource2'
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 8<<20, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
for n in (1<<20, 8<<20):
    t0=time.time()
    data=m[0:n]
    t1=time.time()
    dt=max(t1-t0, 1e-9)
    print('guest_read n=%u dt=%.6f s %.1f MiB/s md5=%s first=%s' % (
        n, dt, n/dt/(1<<20), hashlib.md5(data).hexdigest()[:8], data[:4].hex()))
# write 1MiB of 0xa5 back
pat=b'\xa5'*(1<<20)
t0=time.time()
m[0:1<<20]=pat
t1=time.time()
dt=max(t1-t0, 1e-9)
print('guest_write n=%u dt=%.6f s %.1f MiB/s' % (1<<20, dt, (1<<20)/dt/(1<<20)))
m.close(); os.close(fd)
PY"

echo "=== host verify guest write ==="
python3 - <<'PY'
import os, mmap, time, hashlib
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 8<<20, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
t0=time.time()
data=m[0:1<<20]
t1=time.time()
dt=max(t1-t0, 1e-9)
print("host_read 1MiB dt=%.6f s %.1f MiB/s first=%s" % (dt, (1<<20)/dt/(1<<20), data[:4].hex()))
print("guest_write_visible", data[:4]==b"\xa5"*4)
m.close(); os.close(fd)
PY

$SSH root@172.31.1.1 'rmmod alut_gmap'
ping -c 1 -W 1 172.31.1.1
echo BW_DONE
