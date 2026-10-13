#!/bin/bash
# SPDX-License-Identifier: MIT
# Read-only NTB direction probe, then optional guest LUT (do_map=1).
# Does not cat DMA debugfs, does not rmmod host plx87xx, does not write BAR2.
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo "=== host kernel / mbox / nodes ==="
uname -r
uptime
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info 2>/dev/null || echo 'no mbox_info'
vcactl --skip-modprobe-check status 2>/dev/null | head -20
echo "=== host BAR2 read-only at 0x22000000 ==="
if [ -x /tmp/vca_compute/alut_host_bar ]; then
  /tmp/vca_compute/alut_host_bar 0x22000000
else
  python3 - <<'PY'
import os, mmap, struct
off=0x22000000
path="/sys/bus/pci/devices/0000:05:00.0/resource2"
fd=os.open(path, os.O_RDONLY)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
print("host bar2", hex(off), "word0=0x%08x" % struct.unpack_from("<I", m, 0)[0],
      "bytes", m[:16].hex())
m.close(); os.close(fd)
PY
fi

echo "=== guest node0 peek ==="
$SSH root@172.31.1.1 'uname -r
echo --- iomem 4c ---
grep -n "4c000000\|3e00000000\|01:00.0" /proc/iomem | head -20
echo --- kallsyms ---
grep -E "plx_add_a_lut_entry|plx_del_a_lut_entry" /proc/kallsyms
echo --- dmesg alut ---
dmesg | grep -E "link_side|a_lut|A-LUT|programmed a lut" | tail -20
echo --- alm_state head ---
head -8 /sys/kernel/debug/plx87xx/0000:01:00.0/alm_state
echo --- try guest_pa 0x4c000000 via /dev/mem ---
python3 - <<'"'"'PY'"'"'
import os, mmap, struct
off=0x4c000000
print("try /dev/mem guest_pa", hex(off))
try:
    fd=os.open("/dev/mem", os.O_RDONLY)
    m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
    print("mem", m[:16].hex(), "word0=0x%08x" % struct.unpack_from("<I", m, 0)[0])
    m.close(); os.close(fd)
except Exception as e:
    print("devmem err", type(e).__name__, e)
PY
echo --- lspci ntb ---
lspci -n | grep 295
'

echo "=== ping after peek ==="
ping -c 1 -W 1 172.31.1.1
echo "=== temps (cores only) ==="
vcactl temp 2>/dev/null | grep -E "Core [0-9]" | head -20
echo NTB_DIR_PEEK_DONE
