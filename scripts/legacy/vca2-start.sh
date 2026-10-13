#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

# Intel VCA2 homelab startup helper
# Adjust these values if your environment differs.

KNOWN_GOOD_KERNEL="7.0.2-6-pve"
VCACTL="/usr/sbin/vcactl"
MODULE_LOADER="/usr/sbin/vca2-load-modules"
IMAGE_BASE="/tank-red/vmdata/vca/images"
CARD=0

# The live images. These are per-node reflink copies of a golden master built
# by compute/image/build_node_image.sh -- see docs/IMAGE_BUILDER.md.
# The stock Intel 16.04 images (node{0,1,2}.vcad) are never modified and
# remain the rollback: set VCA_IMAGE_SUFFIX="" to boot them instead.
#
# Getting this wrong is silent: the card comes up healthy on the WRONG OS and
# nothing complains. It pointed at the 16.04 images for a while after all
# three nodes had been moved to 22.04.
IMAGE_SUFFIX="${VCA_IMAGE_SUFFIX-"-ubuntu-jammy"}"
IMAGES=(
  "${IMAGE_BASE}/node0${IMAGE_SUFFIX}.vcad"
  "${IMAGE_BASE}/node1${IMAGE_SUFFIX}.vcad"
  "${IMAGE_BASE}/node2${IMAGE_SUFFIX}.vcad"
)
for _img in "${IMAGES[@]}"; do
  [ -f "$_img" ] || { echo "ERROR: image not found: $_img" >&2; exit 1; }
done

log() { printf '[vca2-start] %s\n' "$*"; }
fail() { printf '[vca2-start] ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run as root"
[[ -x "$VCACTL" ]] || fail "vcactl not found at $VCACTL"

running_kernel="$(uname -r)"
if [[ "$running_kernel" != "$KNOWN_GOOD_KERNEL" ]]; then
  fail "Running kernel is $running_kernel. Known-good VCA2 kernel is $KNOWN_GOOD_KERNEL. Refusing automatic boot."
fi

for image in "${IMAGES[@]}"; do
  [[ -f "$image" ]] || fail "Missing VCA image: $image"
done

if ! lsmod | grep -q '^vop_bus'; then
  [[ -x "$MODULE_LOADER" ]] || fail "VCA module loader missing: $MODULE_LOADER"
  log "Loading VCA2 host modules"
  "$MODULE_LOADER"
  sleep 2
fi

log "Current VCA state:"
"$VCACTL" status || true

# Bring the card/node CPUs into a known BIOS-ready state.
# This is intentionally done before opening the BlockIO disks.
log "Resetting VCA nodes"
"$VCACTL" reset || true

log "Waiting for node BIOS"
"$VCACTL" wait-BIOS

# Open vcablk0 only when not already active for that node.
for cpu in 0 1 2; do
  image="${IMAGES[$cpu]}"

  if "$VCACTL" blockio list "$CARD" "$cpu" 2>/dev/null | grep -qE '^\s*0\s+'"$cpu"'\s+vcablk0\s+active'; then
    log "Card $CARD CPU $cpu vcablk0 already active"
  else
    log "Opening $image as vcablk0 on Card $CARD CPU $cpu"
    "$VCACTL" blockio open "$CARD" "$cpu" vcablk0 RW "$image"
  fi
done

log "BlockIO mappings:"
"$VCACTL" blockio list

for cpu in 0 1 2; do
  log "Booting Card $CARD CPU $cpu from vcablk0"
  "$VCACTL" boot "$CARD" "$cpu" vcablk0
 done

log "Waiting for VCA networking to become ready"
"$VCACTL" wait

log "Final status:"
"$VCACTL" status

if "$VCACTL" status | grep -qv 'STATE: net_device_ready'; then
  fail "At least one node did not reach net_device_ready"
fi

log "All three VCA2 nodes are ready"

# Host virtio max_mtu defaults to 1500; guests are already 65521.
JUMBO_KO="${JUMBO_KO:-/root/vca2/jumbo_mtu.ko}"
if [[ -f "$JUMBO_KO" ]]; then
  log "Applying jumbo MTU 65521 on eth0/1/2"
  rmmod jumbo_mtu 2>/dev/null || true
  insmod "$JUMBO_KO" ifnames=eth0,eth1,eth2 mtu=65521 || log "jumbo_mtu insmod failed (NICs missing?)"
else
  log "No $JUMBO_KO — host VCA NICs stay at MTU 1500"
fi
