#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Deploy a built node image to one VCA node, safely.
#
#     ./deploy_node_image.sh --node 1 --master /path/to/node-ubuntu-jammy.vcad --verify
#     ./deploy_node_image.sh --card 1 --node 0 --master ... --verify   (second card)
#
# Nodes are numbered globally by card: card C, cpu N is node C*3+N, at
# 172.31.(C*3+N+1).1 with image node<C*3+N>-<profile>.vcad -- which is
# vcactl's own default net_config for a second card (172.31.4-6.1).
#
# ---------------------------------------------------------------------------
# WHY THIS EXISTS
#
# The manual sequence is six vcactl commands with two traps that have already
# cost real time, and one that would cost a filesystem:
#
#  1. GOLDEN MASTER, NEVER BOOTED. BlockIO opens an image READ-WRITE. Point
#     two nodes at one file and two kernels write one ext4 -- guaranteed
#     corruption. Point a node at the builder's output and the next build
#     writes into a disk a node has mounted. So: the master is a template,
#     every node gets its own copy, and this script REFUSES to run if the
#     master is currently attached anywhere.
#
#  2. pwrbtn-short IS A TOGGLE, AND A PRESS DURING powering_down IS EATEN.
#     "link_down" is NOT off -- it is on the way down. Pressing then does
#     nothing visible and you sit watching power_off forever wondering why
#     the node will not start. Wait for a SETTLED power_off.
#
#  3. Each image swap changes the node's SSH host key. Cleared here.
#
# On ZFS with feature@block_cloning (or any reflink-capable fs) the per-node
# copy is effectively free -- no extra space, instant. Falls back to a sparse
# copy elsewhere.
set -euo pipefail

CARD=0; NODE=""; MASTER=""; DO_VERIFY=0; DEST=""
NODE_PW="${NODE_PW:-vista1}"

usage(){ sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit "${1:-0}"; }
say(){ echo -e "\n=== $* ==="; }
die(){ echo "ERROR: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --card|-c)   CARD="$2"; shift 2 ;;
        --node|-n)   NODE="$2"; shift 2 ;;
        --master|-m) MASTER="$2"; shift 2 ;;
        --dest|-d)   DEST="$2"; shift 2 ;;
        --verify)    DO_VERIFY=1; shift ;;
        --help|-h)   usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "must run as root"
command -v vcactl >/dev/null || die "vcactl not found -- run this on the host"
case "${CARD}" in 0|1) ;; *) die "--card must be 0 or 1" ;; esac
case "${NODE}" in 0|1|2) ;; *) die "--node must be 0, 1 or 2 (the cpu on that card)" ;; esac
GNODE=$((CARD * 3 + NODE))
[ -f "${MASTER}" ] || die "--master image not found: ${MASTER}"

IMG_DIR="$(dirname "${MASTER}")"
DEST="${DEST:-${IMG_DIR}/node${GNODE}-$(basename "${MASTER}" .vcad | sed 's/^node-//').vcad}"
IP="172.31.$((GNODE + 1)).1"

state(){ vcactl status 2>/dev/null | awk -v c="${CARD}" -v n="${NODE}" '$0 ~ ("Card: " c " Cpu: " n " ") {print $NF}'; }

# A master that is open RW somewhere is being written to. Copying it would
# capture a torn filesystem, and rebuilding over it would corrupt a live node.
say "Preflight"
if vcactl blockio list 2>/dev/null | grep -F "${MASTER}" | grep -qi active; then
    vcactl blockio list | grep -F "${MASTER}" | sed 's/^/  /'
    die "master is ATTACHED to a node. Detach it first:
    vcactl blockio close <card> <cpu> vcablk0
  The master must stay a template that nothing boots."
fi
echo "  master:  ${MASTER}"
echo "  node:    card ${CARD} cpu ${NODE} = node ${GNODE} (${IP})"
echo "  dest:    ${DEST}"
echo "  state:   $(state)"

say "Powering node ${NODE} off"
s="$(state)"
if [ "$s" != "power_off" ]; then
    # Only press if it is not already on its way down; a press during
    # powering_down is consumed and the node comes back up instead.
    case "$s" in
        powering_down|link_down) echo "  already going down (${s}), not pressing" ;;
        *) echo "  pressing power (${s})"; vcactl pwrbtn-short "${CARD}" "${NODE}" >/dev/null 2>&1 ;;
    esac
    for _ in $(seq 1 60); do
        s="$(state)"; [ "$s" = "power_off" ] && break
        sleep 5
    done
fi
s="$(state)"
[ "$s" = "power_off" ] || die "node ${NODE} is '${s}', not a settled power_off. Not touching its disk."
echo "  settled: power_off"

say "Detaching current image"
vcactl blockio close "${CARD}" "${NODE}" vcablk0 2>&1 | tail -1 || true
sleep 3

say "Copying master -> per-node image"
rm -f "${DEST}"
if cp --reflink=always "${MASTER}" "${DEST}" 2>/dev/null; then
    echo "  reflink copy (instant, shares blocks with the master)"
else
    cp --sparse=always "${MASTER}" "${DEST}"
    echo "  sparse copy (no reflink support on this filesystem)"
fi
echo "  $(du -h --apparent-size "${DEST}" | cut -f1) apparent / $(du -h "${DEST}" | cut -f1) on disk"

say "Attaching and booting"
vcactl blockio open "${CARD}" "${NODE}" vcablk0 RW "${DEST}" 2>&1 | tail -1
sleep 2
vcactl blockio list | grep -E "^ +${CARD} +${NODE} +vcablk0" | sed 's/^/  /'

vcactl pwrbtn-short "${CARD}" "${NODE}" >/dev/null 2>&1
for _ in $(seq 1 40); do
    s="$(state)"
    case "$s" in bios_ready|bios_up|waiting_for_boot) break ;; esac
    sleep 5
done
echo "  ${s}"
vcactl wait-BIOS "${CARD}" "${NODE}" 2>&1 | tail -1
vcactl boot "${CARD}" "${NODE}" vcablk0 2>&1 | tail -1

for _ in $(seq 1 60); do
    s="$(state)"; [ "$s" = "net_device_ready" ] && break
    sleep 6
done
s="$(state)"
[ "$s" = "net_device_ready" ] || die "node ${NODE} reached '${s}', not net_device_ready.
  The image is at ${DEST}; mount it on the host and read
  /var/log/vca-netcfg.log -- there is no console on these nodes."
echo "  >>> net_device_ready <<<"

# A different OS means a different host key; without this every later ssh
# fails with REMOTE HOST IDENTIFICATION HAS CHANGED.
ssh-keygen -f /root/.ssh/known_hosts -R "${IP}" >/dev/null 2>&1 || true

if [ "${DO_VERIFY}" -eq 1 ]; then
    say "Verifying on the node"
    command -v sshpass >/dev/null || die "sshpass not installed; run the verifier by hand"
    sshpass -p "${NODE_PW}" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=25 \
        "root@${IP}" /opt/vca-tools/verify_node_image.sh
fi

say "Done"
echo "card ${CARD} cpu ${NODE} (node ${GNODE}, ${IP}) -> ${DEST}"
vcactl status
