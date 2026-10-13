#!/bin/bash
# SPDX-License-Identifier: MIT
python3 - <<'PY'
import os, mmap, struct
path="/dev/vca_alut_0000_05_00_0"
fd=os.open(path, os.O_RDWR)
m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ|mmap.PROT_WRITE, offset=0)
struct.pack_into("<IIII", m, 0, 0x414c5554, 0, 0, 0)
print("restored magic=0x%x seq=%u" % struct.unpack_from("<II", m, 0))
m.close(); os.close(fd)
PY
echo mbox_info:
cat /sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info
ping -c 1 -W 1 172.31.1.1
vcactl --skip-modprobe-check status | head -5
echo RESTORED
