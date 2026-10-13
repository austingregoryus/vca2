#!/bin/bash
# SPDX-License-Identifier: MIT
# Safe dumps only. See docs/GOTCHAS.md.
set -euo pipefail
OUT=/root/vca_fabric_dump
mkdir -p "$OUT"
date -Is > "$OUT/when.txt"
uname -a >> "$OUT/when.txt"
vcactl --skip-modprobe-check status > "$OUT/vcactl_status.txt" 2>&1 || true
for bdf in 0000:05:00.0 0000:07:00.0 0000:0a:00.0; do
  id=$(echo "$bdf" | tr : _)
  cat /sys/kernel/debug/plx87xx/$bdf/spad > "$OUT/spad_$id.txt"
  cat /sys/kernel/debug/plx87xx/$bdf/alm_state > "$OUT/alm_$id.txt"
  cat /sys/kernel/debug/plx87xx/$bdf/msi_irq_info > "$OUT/msi_$id.txt" 2>/dev/null || true
done
for n in 1 2 3; do
  cat /sys/kernel/debug/vop$n/stats > "$OUT/vop${n}_stats.txt" 2>/dev/null || true
done
ls -l "$OUT"
echo "$OUT"
