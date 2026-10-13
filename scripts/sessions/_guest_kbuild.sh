#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
$SSH root@172.31.1.1 'uname -r
ls -d /lib/modules/$(uname -r)/build 2>/dev/null || echo no_headers
ls /dev/mem
cat /proc/cmdline
sysctl kernel.strict_devmem 2>/dev/null || true
# try /dev/mem read of BAR2+off if possible
python3 - <<'"'"'PY'"'"'
import os, mmap
off=0x3e00000000+0x22000000
print("try /dev/mem at", hex(off))
try:
    fd=os.open("/dev/mem", os.O_RDONLY)
    m=mmap.mmap(fd, 4096, mmap.MAP_SHARED, mmap.PROT_READ, offset=off)
    print("mem", m[:16].hex())
    m.close(); os.close(fd)
except Exception as e:
    print("devmem err", type(e).__name__, e)
PY
ping -c 1 -W 1 127.0.0.1 >/dev/null
'
echo ping_host_to_node
ping -c 1 -W 1 172.31.1.1
