#!/bin/bash
# SPDX-License-Identifier: MIT
# Node 0: ALUT header + doorbell 15. Guest waiter acks BAR2 on IRQ.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo "=== guest LUT + doorbell 15 ==="
$SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
rmmod alut_gdb 2>/dev/null || true
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 pci_did=0x2958 host_dma=0x4c000000 host_sz=0x2000000
insmod ./alut_gdb.ko pci_did=0x2958 db=15
dmesg | grep -E "alut_gmap: LUT|alut_gdb: db=" | tail -4
'
GLINE=$($SSH root@172.31.1.1 'dmesg | grep "alut_gmap: LUT" | tail -1')
echo "$GLINE"
PEER=$(echo "$GLINE" | sed -n 's/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p')
echo "GUEST_PEER=$PEER"
if [ -z "$PEER" ]; then echo NO_PEER; exit 1; fi

rmmod alut_hdb 2>/dev/null || true
insmod /tmp/vca_compute/alut_hmap/alut_hdb.ko pci_did=0x2954 db=15

echo "=== start guest waiter ==="
$SSH root@172.31.1.1 "python3 -u - <<'PY'
import os, mmap, struct, signal, sys
off=0x$PEER
path='/sys/bus/pci/devices/0000:01:00.0/resource2'
bar=os.open(path, os.O_RDWR)
m=mmap.mmap(bar, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
fd=os.open('/dev/vca_gdb', os.O_RDONLY)
open('/tmp/gdbw.pid','w').write(str(os.getpid()))
sys.stdout.write('WAITER_READY\\n'); sys.stdout.flush()
while True:
    os.read(fd, 4)
    magic, seq, cmd, st = struct.unpack_from('<IIII', m, 0)
    if magic == 0x414c5554 and cmd == 1:
        struct.pack_into('<I', m, 12, 2)
PY" >/tmp/gdbw.log 2>&1 &
for i in 1 2 3 4 5 6 7 8 9 10; do
  grep -q WAITER_READY /tmp/gdbw.log 2>/dev/null && break
  sleep 0.2
done
cat /tmp/gdbw.log || true

echo "=== 50 doorbell RTTs ==="
python3 - <<'PY'
import os, mmap, struct, time, sys
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
kick_path="/sys/kernel/debug/alut_hdb/kick"
dts=[]
fails=0
for i in range(1, 51):
    struct.pack_into("<IIII", m, 0, 0x414c5554, i, 1, 0)
    t0=time.perf_counter()
    kf=os.open(kick_path, os.O_WRONLY)
    os.write(kf, b"1\n")
    os.close(kf)
    ok=False
    while time.perf_counter()-t0 < 0.5:
        magic, seq, cmd, st = struct.unpack_from("<IIII", m, 0)
        if st == 2 and seq == i:
            ok=True
            break
    dt=time.perf_counter()-t0
    if ok:
        dts.append(dt)
    else:
        fails += 1
        print("FAIL iter", i, "st", st, "seq", seq)
        break
if dts:
    dts.sort()
    avg=sum(dts)/len(dts)
    print("DB_RTT n=%d fail=%d min=%.3f ms avg=%.3f ms p50=%.3f ms max=%.3f ms" % (
        len(dts), fails, dts[0]*1e3, avg*1e3, dts[len(dts)//2]*1e3, dts[-1]*1e3))
    print("DOORBELL_RTT_OK" if fails==0 else "DOORBELL_RTT_FAIL")
else:
    print("DOORBELL_RTT_FAIL no samples")
m.close(); os.close(fd)
PY

echo "=== cleanup ==="
$SSH root@172.31.1.1 'kill $(cat /tmp/gdbw.pid 2>/dev/null) 2>/dev/null || true; rmmod alut_gdb; rmmod alut_gmap; dmesg | grep -E "alut_g" | tail -6'
rmmod alut_hdb
echo "=== pings ==="
ping -c 1 -W 1 172.31.1.1
ping -c 1 -W 1 172.31.2.1
ping -c 1 -W 1 172.31.3.1
vcactl --skip-modprobe-check status | head -5
echo DB_RTT_DONE
