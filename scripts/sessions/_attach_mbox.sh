#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
MBOX=/tank-red/vmdata/vca/mbox0.bin
if [[ ! -f "$MBOX" ]]; then
  dd if=/dev/zero of="$MBOX" bs=1M count=16 status=none
  echo "created $MBOX"
fi
ls -l "$MBOX"
echo '=== blockio before ==='
vcactl blockio list
echo '=== open vcablk1 on cpu 0 ==='
vcactl blockio open 0 0 vcablk1 RW "$MBOX"
echo '=== blockio after ==='
vcactl blockio list
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'sleep 1; ls -l /dev/vcablk*; dmesg | grep -i vcablk | tail -20'
