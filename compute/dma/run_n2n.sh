#!/bin/bash
# SPDX-License-Identifier: MIT
# Node-to-node bulk transfer staged through one shared host buffer.
# Both nodes map the SAME host DMA address through their own A-LUT window,
# then each uses its own card-side DMA engine.
set -u
source "$(dirname "$0")/preflight.sh"
SRC=${1:-1}; SDID=${2:-0x2958}     # sender   node index (1..3) and its NTB did
DST=${3:-2}; DDID=${4:-0x2959}     # receiver node index and its NTB did
TAG=${5:-0xab}
CD=/tmp/vca_compute/dma
S() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 172.31.$SRC.1 "$@"; }
D() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 172.31.$DST.1 "$@"; }

cleanup() {
  echo "--- teardown ---"
  S "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  D "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  sleep 1
  rmmod hostbuf 2>/dev/null
  echo "host=$(lsmod | cut -d" " -f1 | grep -cx hostbuf) src=$(S 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"') dst=$(D 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"')"
  vcactl status
}
preflight "host:hostbuf.ko" "node:${SRC}:alut_gmap.ko,n2n.ko" "node:${DST}:alut_gmap.ko,n2n.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== shared host buffer ==="
insmod $CD/hostbuf.ko size=0x400000 || exit 1
HDMA=$(awk '/^dma/{print $2}' /sys/kernel/debug/vca_hostbuf/info)
echo "host dma = $HDMA"

echo "=== A-LUT window on sender (node $((SRC-1))) ==="
S "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=$SDID" || exit 1
SOFF=$(S "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "sender peer_off = $SOFF"

echo "=== A-LUT window on receiver (node $((DST-1))) ==="
D "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=$DDID" || exit 1
DOFF=$(D "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "receiver peer_off = $DOFF"
[ -z "$SOFF" -o -z "$DOFF" ] && { echo "missing peer_off"; exit 1; }

echo
echo "=== SEND from node $((SRC-1)) ==="
S "timeout 120 insmod $CD/n2n.ko mode=0 peer_off=$SOFF ntb_did=$SDID tag=$TAG iters=20"
S "dmesg | grep 'n2n:' | tail -3"

echo
echo "=== RECV on node $((DST-1)) (verifies sender's pattern) ==="
D "timeout 120 insmod $CD/n2n.ko mode=1 peer_off=$DOFF ntb_did=$DDID tag=$TAG iters=20"
D "dmesg | grep 'n2n:' | tail -3"
