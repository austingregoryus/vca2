#!/bin/bash
# SPDX-License-Identifier: MIT
# Doorbell+ALUT 10-iter RTT on nodes 1 and 2. db=15. No plx intr_* debugfs.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

run_one() {
  local ip=$1 gdid=$2 hdid=$3 dma=$4 sz=$5 hostdev=$6 name=$7
  echo "======== $name $ip ========"
  $SCP /tmp/vca_compute/alut_gdb.c /tmp/vca_compute/alut_gmap.c /tmp/vca_compute/Makefile.guest root@$ip:/tmp/alut_gmap/ || \
    ($SSH root@$ip 'mkdir -p /tmp/alut_gmap'; $SCP /tmp/vca_compute/alut_gdb.c /tmp/vca_compute/alut_gmap.c /tmp/vca_compute/Makefile.guest root@$ip:/tmp/alut_gmap/)
  $SSH root@$ip "set -e
cd /tmp/alut_gmap
cp Makefile.guest Makefile
make
rmmod alut_gdb 2>/dev/null || true
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 pci_did=$gdid host_dma=$dma host_sz=$sz
insmod ./alut_gdb.ko pci_did=$gdid db=15
dmesg | grep -E 'alut_gmap: LUT|alut_gdb: db=' | tail -3
"
  local line peer
  line=$($SSH root@$ip 'dmesg | grep "alut_gmap: LUT" | tail -1')
  echo "$line"
  peer=$(echo "$line" | sed -n 's/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p')
  if [ -z "$peer" ]; then echo NO_PEER; return 1; fi
  rmmod alut_hdb 2>/dev/null || true
  insmod /tmp/vca_compute/alut_hmap/alut_hdb.ko pci_did=$hdid db=15

  $SSH root@$ip "python3 -u - <<'PY'
import os, mmap, struct, sys
off=0x$peer
bar=os.open('/sys/bus/pci/devices/0000:01:00.0/resource2', os.O_RDWR)
m=mmap.mmap(bar, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=off)
fd=os.open('/dev/vca_gdb', os.O_RDONLY)
open('/tmp/gdbw.pid','w').write(str(os.getpid()))
sys.stdout.write('WAITER_READY\\n'); sys.stdout.flush()
while True:
    os.read(fd, 4)
    magic, seq, cmd, st = struct.unpack_from('<IIII', m, 0)
    if magic == 0x414c5554 and cmd == 1:
        struct.pack_into('<I', m, 12, 2)
PY" >/tmp/gdbw_$name.log 2>&1 &
  for i in 1 2 3 4 5 6 7 8 9 10; do
    grep -q WAITER_READY /tmp/gdbw_$name.log 2>/dev/null && break
    sleep 0.2
  done
  cat /tmp/gdbw_$name.log || true

  python3 - <<PY
import os, mmap, struct, time
path="$hostdev"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
kick_path="/sys/kernel/debug/alut_hdb/kick"
dts=[]
fails=0
for i in range(1, 11):
    struct.pack_into("<IIII", m, 0, 0x414c5554, i, 1, 0)
    t0=time.perf_counter()
    kf=os.open(kick_path, os.O_WRONLY)
    os.write(kf, b"1\\n")
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
        print("FAIL iter", i)
        break
if dts:
    dts.sort()
    avg=sum(dts)/len(dts)
    print("$name DB_RTT n=%d fail=%d min=%.3f ms avg=%.3f ms max=%.3f ms" % (
        len(dts), fails, dts[0]*1e3, avg*1e3, dts[-1]*1e3))
    print("$name", "DOORBELL_OK" if fails==0 else "DOORBELL_FAIL")
else:
    print("$name DOORBELL_FAIL")
m.close(); os.close(fd)
PY

  $SSH root@$ip 'kill $(cat /tmp/gdbw.pid 2>/dev/null) 2>/dev/null || true; rmmod alut_gdb; rmmod alut_gmap'
  rmmod alut_hdb 2>/dev/null || true
  ping -c 1 -W 1 $ip
}

run_one 172.31.2.1 0x2959 0x2955 0x48000000 0x2000000 /dev/vca_alut_0000_07_00_0 n1
run_one 172.31.3.1 0x295a 0x2956 0x47000000 0x1000000 /dev/vca_alut_0000_0a_00_0 n2
vcactl --skip-modprobe-check status | head -5
echo DB_N12_DONE
