#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== sysfs pread resource2 and resource2_wc at 0x22000000 ==='
$SSH root@172.31.1.1 'python3 - <<'"'"'PY'"'"'
import os
off=0x22000000
for name in ("resource2","resource2_wc"):
    path="/sys/bus/pci/devices/0000:01:00.0/"+name
    fd=os.open(path, os.O_RDONLY)
    try:
        data=os.pread(fd, 16, off)
    except Exception as e:
        print(name, "pread err", e)
        os.close(fd)
        continue
    os.close(fd)
    print(name, "len", len(data), data.hex())
# also offset 0 read-only 16 bytes
fd=os.open("/sys/bus/pci/devices/0000:01:00.0/resource2", os.O_RDONLY)
data=os.pread(fd, 16, 0)
os.close(fd)
print("resource2@0", data.hex())
PY
'
echo '=== ping ==='
ping -c 1 -W 1 172.31.1.1
