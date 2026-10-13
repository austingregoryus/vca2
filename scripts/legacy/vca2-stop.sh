#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

VCACTL="/usr/sbin/vcactl"
CARD=0

log() { printf '[vca2-stop] %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

log "Requesting node shutdown"
for cpu in 0 1 2; do
  "$VCACTL" shutdown "$CARD" "$cpu" 2>/dev/null || true
done

sleep 5

log "Closing BlockIO disks where possible"
for cpu in 0 1 2; do
  "$VCACTL" blockio close "$CARD" "$cpu" vcablk0 2>/dev/null || true
done

log "Current status"
"$VCACTL" status || true
