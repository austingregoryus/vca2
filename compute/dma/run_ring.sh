#!/bin/bash
# SPDX-License-Identifier: MIT
# Pipelined node-to-node transfer over a host-staged ring.
# Receiver is started FIRST (it waits for the sender to publish slot 0), then
# the sender. Both run concurrently; the end-to-end rate is the receiver's.
set -u
source "$(dirname "$0")/preflight.sh"
CD=/tmp/vca_compute/dma
SRC=${1:-1}; SDID=${2:-0x2958}
DST=${3:-2}; DDID=${4:-0x2959}
TAG=${5:-0x5a}
ITERS=${6:-64}
S() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 172.31.$SRC.1 "$@"; }
D() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 172.31.$DST.1 "$@"; }

cleanup() {
  echo; echo "--- teardown ---"
  S "rmmod ring 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  D "rmmod ring 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  sleep 1
  rmmod hostbuf 2>/dev/null
  echo "leftovers host=$(lsmod | cut -d" " -f1 | grep -cx hostbuf) src=$(S 'lsmod | cut -d" " -f1 | grep -cxE "ring|alut_gmap"') dst=$(D 'lsmod | cut -d" " -f1 | grep -cxE "ring|alut_gmap"')"
  vcactl status
}
preflight "host:hostbuf.ko" "node:${SRC}:alut_gmap.ko,ring.ko" "node:${DST}:alut_gmap.ko,ring.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== shared host ring buffer (4 MiB) ==="
insmod $CD/hostbuf.ko size=0x400000 || exit 1
HDMA=$(awk '/^dma/{print $2}' /sys/kernel/debug/vca_hostbuf/info)
echo "host dma = $HDMA"

S "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=$SDID" || exit 1
D "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=$DDID" || exit 1
SOFF=$(S "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
DOFF=$(D "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "sender peer_off=$SOFF  receiver peer_off=$DOFF"

echo
echo "=== start receiver (waits for slot 0), then sender ==="
( D "timeout 120 insmod $CD/ring.ko mode=1 peer_off=$DOFF ntb_did=$DDID tag=$TAG iters=$ITERS" ) &
RP=$!
sleep 2
( S "timeout 120 insmod $CD/ring.ko mode=0 peer_off=$SOFF ntb_did=$SDID tag=$TAG iters=$ITERS" ) &
SP=$!
wait $RP $SP

echo
echo "--- sender ---";   S "dmesg | grep 'ring:' | tail -3"
echo "--- receiver ---"; D "dmesg | grep 'ring:' | tail -3"
