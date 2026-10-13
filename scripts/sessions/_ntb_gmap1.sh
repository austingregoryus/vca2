#!/bin/bash
# SPDX-License-Identifier: MIT
# Guest A-LUT map of host DMA 0x4c000000, then READ-ONLY BAR2 at peer_off.
# rmmod deletes the entry. No host rmmod, no BAR writes, no DMA debugfs.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

$SSH root@172.31.1.1 'set -e
cd /tmp/alut_gmap
rmmod alut_gmap 2>/dev/null || true
insmod ./alut_gmap.ko do_map=1 host_dma=0x4c000000 host_sz=0x2000000
dmesg | grep alut_gmap | tail -8
echo --- alm after map ---
grep -E "current mappings|^\[[0-9]+\] from:.*ref_cnt:[1-9]" /sys/kernel/debug/plx87xx/0000:01:00.0/alm_state
echo --- parse peer ---
PEER=$(dmesg | grep "alut_gmap: LUT host_dma" | tail -1 | sed -n "s/.*peer_off=0x\([0-9a-fA-F]*\).*/\1/p")
echo PEER_OFF=$PEER
if [ -z "$PEER" ]; then echo NO_PEER; lsmod | grep alut; exit 1; fi
python3 - <<PY
import os, mmap, struct, sys
off=int("$PEER", 16)
path="/sys/bus/pci/devices/0000:01:00.0/resource2"
print("guest resource2 mmap off=0x%x" % off)
try:
    fd=os.open(path, os.O_RDONLY)
    m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
    w=struct.unpack_from("<I", m, 0)[0]
    print("mmap word0=0x%08x bytes=%s" % (w, m[:16].hex()))
    m.close(); os.close(fd)
except Exception as e:
    print("mmap err", type(e).__name__, e)
print("try /dev/mem BAR2+off")
bar=0x3e00000000+off
try:
    fd=os.open("/dev/mem", os.O_RDONLY)
    m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=bar)
    w=struct.unpack_from("<I", m, 0)[0]
    print("devmem 0x%x word0=0x%08x bytes=%s" % (bar, w, m[:16].hex()))
    m.close(); os.close(fd)
except Exception as e:
    print("devmem err", type(e).__name__, e)
PY
echo --- ping local ---
ping -c 1 -W 1 127.0.0.1 >/dev/null
rmmod alut_gmap
dmesg | grep alut_gmap | tail -3
echo GMAP1_DONE
'
echo "=== ping host->node0 ==="
ping -c 2 -W 1 172.31.1.1
echo "=== host mbox still ==="
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info
echo GMAP1_HOST_DONE
