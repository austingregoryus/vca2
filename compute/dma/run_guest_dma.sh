#!/bin/bash
# SPDX-License-Identifier: MIT
# Guest-side DMA benchmark across the NTB.
#   host: hostbuf.ko -> coherent buffer, publishes its bus address
#   guest: alut_gmap.ko -> A-LUT window onto that address, reports peer_off
#   guest: dmabench2.ko -> measures
# Teardown is strictly reverse: guest window BEFORE host buffer, always.
set -u
source "$(dirname "$0")/preflight.sh"
NODE=${1:-1}                 # 1,2,3  -> 172.31.N.1
DID=${2:-0x2958}             # guest NTB did: n0 2958, n1 2959, n2 295a
IP=172.31.$NODE.1
G() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $IP "$@"; }
CD=/tmp/vca_compute/dma

cleanup() {
  echo "--- teardown ---"
  G "rmmod dmabench2 2>/dev/null; rmmod dmabench 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  sleep 1
  rmmod hostbuf 2>/dev/null
  echo "modules left: host=$(lsmod | cut -d" " -f1 | grep -cx hostbuf) guest=$(G 'lsmod | cut -d" " -f1 | grep -cxE "dmabench|alut_gmap"')"
  vcactl status
}
preflight "host:hostbuf.ko" "node:${NODE}:alut_gmap.ko,dmabench2.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== host buffer ==="
insmod /tmp/vca_compute/dma/hostbuf.ko size=0x400000 || exit 1
HDMA=$(awk '/^dma/{print $2}' /sys/kernel/debug/vca_hostbuf/info)
echo "host dma = $HDMA"

echo "=== guest A-LUT window (node $((NODE-1)), did $DID) ==="
G "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=$DID" || exit 1
POFF=$(G "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "guest peer_off = $POFF"
[ -z "$POFF" ] && { echo "no peer_off"; exit 1; }

echo "=== dmabench2 (pipelined) ==="
G "timeout 180 insmod $CD/dmabench2.ko peer_off=$POFF buf_sz=0x400000 iters=20 ntb_did=$DID"
G "dmesg | grep dmabench2 | tail -45"
