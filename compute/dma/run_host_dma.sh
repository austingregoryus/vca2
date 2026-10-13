#!/bin/bash
# SPDX-License-Identifier: MIT
# Host-side DMA benchmark across the NTB (mirror of run_guest_dma.sh).
#   guest: alut_gmap.ko do_alloc=1 -> 4 MiB guest buffer, reports its PA
#   host:  alut_hmap.ko            -> host A-LUT window onto that PA
#   host:  dmabench2.ko            -> host DMA engine measures
# Teardown reverse: host window, then guest buffer.
set -u
source "$(dirname "$0")/preflight.sh"
NODE=${1:-1}
GDID=${2:-0x2958}     # guest NTB did
HDID=${3:-0x2954}     # host NTB did for that node
IP=172.31.$NODE.1
G() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $IP "$@"; }
CD=/tmp/vca_compute/dma

cleanup() {
  echo "--- teardown ---"
  rmmod dmabench2 2>/dev/null
  rmmod alut_hmap 2>/dev/null
  sleep 1
  G "rmmod alut_gmap 2>/dev/null; true"
  echo "host leftovers=$(lsmod | cut -d" " -f1 | grep -cxE 'dmabench2|alut_hmap') guest leftovers=$(G 'lsmod | cut -d" " -f1 | grep -cx alut_gmap')"
  vcactl status
}
preflight "host:alut_hmap.ko,dmabench2.ko" "node:${NODE}:alut_gmap.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== guest buffer on node $((NODE-1)) ==="
G "insmod $CD/alut_gmap.ko do_alloc=1 host_sz=0x400000 pci_did=$GDID" || exit 1
GPA=$(G "dmesg | grep 'alut_gmap: gbuf dma=' | tail -1" | sed 's/.*gbuf dma=\(0x[0-9a-f]*\).*/\1/')
echo "guest buffer PA = $GPA"
[ -z "$GPA" ] && { echo "no guest PA"; exit 1; }

echo "=== host A-LUT window onto it ==="
insmod /tmp/vca_compute/dma/alut_hmap.ko pci_did=$HDID guest_dma=$GPA size=0x400000 || exit 1
HOFF=$(dmesg | grep 'alut_hmap: LUT guest_dma' | tail -1 | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "host peer_off = $HOFF"
[ -z "$HOFF" ] && { echo "no host peer_off"; exit 1; }

echo "=== dmabench2 on the HOST engine ==="
timeout 180 insmod /tmp/vca_compute/dma/dmabench2.ko \
    peer_off=$HOFF buf_sz=0x400000 iters=20 ntb_did=$HDID maxdepth=4
dmesg | grep dmabench2 | tail -30
