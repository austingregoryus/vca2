#!/bin/bash
# SPDX-License-Identifier: MIT
# Do two card-side DMA engines contend, or do they overlap?
#
# This is the hardware question the chunked node->node pipeline depends on. If
# node 0 pushing and node 1 pulling at the same time each keep ~full speed,
# the engines are independent and a producer/consumer pipeline will reach the
# sender's rate instead of half of it.
#
# Both hit the SAME host buffer on purpose. The data races, which is fine --
# this measures throughput only, not integrity (integrity is proven by
# run_n2n.sh).
set -u
source "$(dirname "$0")/preflight.sh"
CD=/tmp/vca_compute/dma
A() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 172.31.1.1 "$@"; }
B() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 172.31.2.1 "$@"; }

cleanup() {
  echo; echo "--- teardown ---"
  A "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  B "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  sleep 1
  rmmod hostbuf 2>/dev/null
  echo "leftovers host=$(lsmod | cut -d" " -f1 | grep -cx hostbuf) n0=$(A 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"') n1=$(B 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"')"
  vcactl status
}
preflight "host:hostbuf.ko" "node:1:alut_gmap.ko,n2n.ko" "node:2:alut_gmap.ko,n2n.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

insmod $CD/hostbuf.ko size=0x400000 || exit 1
HDMA=$(awk '/^dma/{print $2}' /sys/kernel/debug/vca_hostbuf/info)
A "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=0x2958" || exit 1
B "insmod $CD/alut_gmap.ko do_map=1 host_dma=$HDMA host_sz=0x400000 pci_did=0x2959" || exit 1
SOFF=$(A "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
DOFF=$(B "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "node0 peer_off=$SOFF  node1 peer_off=$DOFF"

echo
echo "########## BASELINE: one engine at a time ##########"
A "insmod $CD/n2n.ko mode=0 peer_off=$SOFF ntb_did=0x2958 tag=0x11 iters=40; rmmod n2n"
A "dmesg | grep 'n2n: SEND' | tail -1"
B "insmod $CD/n2n.ko mode=1 peer_off=$DOFF ntb_did=0x2959 tag=0x11 iters=40; rmmod n2n"
B "dmesg | grep 'n2n: RECV' | tail -1" | sed 's/ VERIFIED.*//; s/ CORRUPT.*//'

echo
echo "########## CONCURRENT: both engines at once ##########"
( A "insmod $CD/n2n.ko mode=0 peer_off=$SOFF ntb_did=0x2958 tag=0x22 iters=40; rmmod n2n" ) &
P1=$!
( B "insmod $CD/n2n.ko mode=1 peer_off=$DOFF ntb_did=0x2959 tag=0x22 iters=40; rmmod n2n" ) &
P2=$!
wait $P1 $P2
echo "node0 (push):"; A "dmesg | grep 'n2n: SEND' | tail -1"
echo "node1 (pull):"; B "dmesg | grep 'n2n: RECV' | tail -1" | sed 's/ VERIFIED.*//; s/ CORRUPT.*//'
