#!/bin/bash
# SPDX-License-Identifier: MIT
# Rigorous version of the 4a probe: same target window, two different writers.
#
#   CONTROL : the HOST's DMA engine writes 0x383d..+H  -> must land in node 1
#   TEST    : node 0's DMA engine writes the same address via its A-LUT window
#
# If CONTROL lands and TEST does not, the window and the IOMMU mapping are
# both fine and the only thing failing is peer-to-peer reflection at the root
# complex. That isolates the variable properly instead of inferring it.
set -u
source "$(dirname "$0")/preflight.sh"
CD=/tmp/vca_compute/dma
N0=172.31.1.1; N0DID=0x2958
N1=172.31.2.1; N1DID=0x2959
HOST_NTB_N1=0x2955
A() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $N0 "$@"; }
B() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 $N1 "$@"; }

cleanup() {
  echo; echo "--- teardown ---"
  A "rmmod n2n 2>/dev/null; rmmod alut_gmap 2>/dev/null; true"
  rmmod dmabench2 2>/dev/null
  rmmod p2pmap 2>/dev/null
  rmmod alut_hmap 2>/dev/null
  B "rmmod alut_gmap 2>/dev/null; true"
  echo "leftovers host=$(lsmod | cut -d" " -f1 | grep -cxE 'alut_hmap|p2pmap|dmabench2') n0=$(A 'lsmod | cut -d" " -f1 | grep -cxE "n2n|alut_gmap"') n1=$(B 'lsmod | cut -d" " -f1 | grep -cx alut_gmap')"
  vcactl status
}
preflight "host:alut_hmap.ko,p2pmap.ko,dmabench2.ko" "node:1:alut_gmap.ko,n2n.ko" "node:2:alut_gmap.ko"

# armed only after preflight: a failed precondition must not run
# teardown on state this script did not create (see SAFETY.md, unload order)
trap cleanup EXIT

echo "=== node 1: target buffer ==="
B "insmod $CD/alut_gmap.ko do_alloc=1 host_sz=0x400000 pci_did=$N1DID" || exit 1
N1PA=$(B "dmesg | grep 'alut_gmap: gbuf dma=' | tail -1" | sed 's/.*gbuf dma=\(0x[0-9a-f]*\).*/\1/')
B "$CD/gbufpeek 2 clear" >/dev/null
echo "node1 buffer PA = $N1PA"

echo "=== host: A-LUT window from node 1's NTB onto it ==="
insmod $CD/alut_hmap.ko pci_did=$HOST_NTB_N1 guest_dma=$N1PA size=0x400000 || exit 1
H=$(dmesg | grep 'alut_hmap: LUT guest_dma' | tail -1 | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
BAR2=$(lspci -s 07:00.0 -v 2>/dev/null | awk '/Memory at/ && /64-bit, prefetchable/ {print $3; exit}')
TARGET=$(printf "0x%x" $((0x$BAR2 + $H)))
echo "host peer_off=$H  BAR2=0x$BAR2  TARGET=$TARGET"

echo
echo "########## CONTROL: host DMA engine writes the window ##########"
timeout 120 insmod $CD/dmabench2.ko peer_off=$H buf_sz=0x400000 iters=2 \
        ntb_did=$HOST_NTB_N1 maxdepth=1 do_verify=0 2>&1
dmesg | grep dmabench2 | tail -2
echo "--- node 1 buffer after HOST write ---"
B "$CD/gbufpeek 4"
rmmod dmabench2 2>/dev/null

echo
echo "=== clear node 1 buffer ==="
B "$CD/gbufpeek 2 clear" | tail -1

echo
echo "########## TEST: node 0 DMA engine writes the SAME address ##########"
insmod $CD/p2pmap.ko ntb_did=0x2954 target_pa=$TARGET size=0x400000 2>&1
dmesg | grep p2pmap | tail -2
A "insmod $CD/alut_gmap.ko do_map=1 host_dma=$TARGET host_sz=0x400000 pci_did=$N0DID" || exit 1
S=$(A "dmesg | grep 'alut_gmap: LUT host_dma' | tail -1" | sed 's/.*peer_off=\(0x[0-9a-f]*\).*/\1/')
echo "node0 peer_off = $S"
A "timeout 60 insmod $CD/n2n.ko mode=0 peer_off=$S ntb_did=$N0DID tag=0x77 iters=2 buf_sz=0x400000"
A "dmesg | grep 'n2n: SEND' | tail -1"
echo "--- node 1 buffer after NODE 0 write (expect 0x77000000 if P2P works) ---"
B "$CD/gbufpeek 4"

echo
echo "=== DMAR faults during the test? ==="
dmesg | grep -c "DMAR:.*fault addr"
dmesg | grep "DMAR:.*fault addr" | tail -2
