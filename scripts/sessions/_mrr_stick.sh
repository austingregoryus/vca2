#!/bin/bash
# SPDX-License-Identifier: MIT
# Does CAP_EXP+8.w MaxReadReq write stick? Node 0 host+guest, restore after.
set -eu
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== host before ==='
setpci -v -s 0000:05:00.0 CAP_EXP+8.w
H=$(setpci -s 0000:05:00.0 CAP_EXP+8.w)
echo "try write 485e (MRR 2048)"
setpci -v -s 0000:05:00.0 CAP_EXP+8.w=485e
echo -n 'readback: '; setpci -s 0000:05:00.0 CAP_EXP+8.w
echo -n 'sysfs config word at PCIe cap: '
python3 - <<'PY'
import os
p="/sys/bus/pci/devices/0000:05:00.0/config"
with open(p,"rb") as f:
    data=f.read()
# find PCIe cap
off=data[0x34]
seen=set()
while off and off not in seen:
    seen.add(off)
    cap=data[off]
    nxt=data[off+1]
    if cap==0x10:
        w=data[off+8] | (data[off+9]<<8)
        print("pcie cap at 0x%x DEVCTL=0x%04x MRR_enc=%d" % (off, w, (w>>12)&7))
        break
    off=nxt
PY
setpci -s 0000:05:00.0 CAP_EXP+8.w=$H
echo "restored $H"

echo '=== guest ==='
$SSH root@172.31.1.1 'set -e
G=$(setpci -s 0000:01:00.0 CAP_EXP+8.w)
echo before $G
setpci -v -s 0000:01:00.0 CAP_EXP+8.w=483f
echo -n after_write" "
setpci -s 0000:01:00.0 CAP_EXP+8.w
python3 - <<'"'"'PY'"'"'
import os
p="/sys/bus/pci/devices/0000:01:00.0/config"
data=open(p,"rb").read()
off=data[0x34]
seen=set()
while off and off not in seen:
    seen.add(off)
    cap=data[off]; nxt=data[off+1]
    if cap==0x10:
        w=data[off+8]|(data[off+9]<<8)
        print("guest pcie cap 0x%x DEVCTL=0x%04x MRR_enc=%d" % (off, w, (w>>12)&7))
        break
    off=nxt
PY
setpci -s 0000:01:00.0 CAP_EXP+8.w=$G
echo restored $G
'
ping -c 1 -W 1 172.31.1.1
echo STICK_DONE
