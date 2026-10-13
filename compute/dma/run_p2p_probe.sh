#!/bin/bash
# SPDX-License-Identifier: MIT
# 4a: does the fabric route node -> node WITHOUT host staging?
#
# Point node 0's A-LUT window at the HOST-DOMAIN address of node 1's NTB BAR2
# instead of at host RAM, then have node 0's DMA engine write into it:
#
#   node0 BAR2 + S  --NT0-->  0x383d00000000 + H  =  node1's host-side BAR2 + H
#                             --(switch must route)-->  NT1 --> node1 RAM
#
# This is ONLY an address argument to plx_add_a_lut_entry. No switch register
# writes, no ACS change, nothing persistent, reversible with rmmod.
#
# WRITE ONLY, deliberately: a posted write that nothing claims is dropped,
# whereas a read would sit on the root port's 50us-50ms completion timeout and
# could wedge the DMA engine (the dma_hang signature). Do not add a read here.
#
# Expected: ACSCtl on 04:11.0 and the root port has ReqRedir+ CmpltRedir+, so
# peer-to-peer TLPs are forced upstream to the root complex. Most likely the
# write is dropped and node 1's buffer stays clean -> clean negative result.
set -u
source "$(dirname "$0")/preflight.sh"
CD=/tmp/vca_compute/dma
N0=172.31.1.1; N0DID=0x2958
N1=172.31.2.1; N1DID=0x2959
HOST_NTB_N1=0x2955                 # node 1's host-side NTB function (07:00.0)
A() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $N0 "$@"; }
B() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $N1 "$@"; }

cleanup() {
  echo "--- teardown ---"
  A "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  rmmod p2pmap 2>/dev/null
  rmmod alut_hmap 2>/dev/null
  B "rmmod alut_gmap 2>/dev/null; true"
  echo "leftovers host=$(lsmod | cut -d" " -f1 | grep -cxE 'alut_hmap|p2pmap') n0=$(A 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"') n1=$(B 'lsmod | cut -d" " -f1 | grep -cx alut_gmap')"
  vcactl status
}
preflight "host:alut_hmap.ko,p2pmap.ko" "node:1:alut_gmap.ko,n2n.ko" "node:2:alut_gmap.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== node 1: allocate a target buffer ==="
B "insmod $CD/alut_gmap.ko do_alloc=1 host_sz=0x400000 pci_did=$N1DID" || exit 1
N1PA=$(B "dmesg | grep 'alut_gmap: gbuf dma=' | tail -1" | sed 's/.*gbuf dma=\(0x[0-9a-f]*\).*/\1/')
echo "node1 buffer PA = $N1PA"
B "$CD/gbufpeek 4 clear"

echo
echo "=== host: window from node 1's NTB onto that buffer ==="
insmod $CD/alut_hmap.ko pci_did=$HOST_NTB_N1 guest_dma=$N1PA size=0x400000 || exit 1
H=$(dmesg | grep 'alut_hmap: LUT guest_dma' | tail -1 | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
BAR2_N1=$(printf "0x%x" $((0x$(setpci -s 07:00.0 18.L 2>/dev/null)$(setpci -s 07:00.0 1c.L 2>/dev/null) )) 2>/dev/null)
BAR2_N1=$(lspci -s 07:00.0 -v 2>/dev/null | awk '/Memory at/ && /64-bit, prefetchable/ {print $3; exit}')
echo "host peer_off into node1 = $H"
echo "node1 host-side BAR2     = 0x$BAR2_N1"
TARGET=$(printf "0x%x" $((0x$BAR2_N1 + $H)))
echo "TARGET (host-domain addr node0 must reach) = $TARGET"

echo
echo "=== host: allow that range in the IOMMU (identity map) ==="
insmod $CD/p2pmap.ko ntb_did=0x2954 target_pa=$TARGET size=0x400000 2>&1 || echo "p2pmap load failed (continuing, expect DMAR faults)"
dmesg | grep p2pmap | tail -3

echo
echo "=== node 0: point its A-LUT window at that target ==="
A "insmod $CD/alut_gmap.ko do_map=1 host_dma=$TARGET host_sz=0x400000 pci_did=$N0DID" || exit 1
S=$(A "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "node0 peer_off = $S"
[ -z "$S" ] && { echo "no node0 peer_off"; exit 1; }

echo
echo "=== node 0: WRITE-ONLY DMA into it (tag 0x77) ==="
A "timeout 60 insmod $CD/n2n.ko mode=0 peer_off=$S ntb_did=$N0DID tag=0x77 iters=2 buf_sz=0x400000"
A "dmesg | grep 'n2n:' | tail -2"

echo
echo "=== did it land in node 1's buffer? (expect 0x77000000 if P2P works) ==="
B "$CD/gbufpeek 8"

echo
echo "=== AER status after (any new errors?) ==="
lspci -s 00:03.2 -vvv 2>/dev/null | grep -E "UESta|CESta" | head -2
lspci -s 04:11.0 -vvv 2>/dev/null | grep -E "UESta|CESta" | head -2
dmesg | tail -5
