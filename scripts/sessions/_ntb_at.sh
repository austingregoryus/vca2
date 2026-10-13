#!/bin/bash
# SPDX-License-Identifier: MIT
# Safe seq_printf dumps only: alm_state, spad, dump_regs_single grep BAR2_AT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== HOST 05:00.0 BAR2_AT 0x0c3c / 0x0c40 ==='
grep -E '\[0x0c3c\]|\[0x0c40\]|\[0x0c38\]' /sys/kernel/debug/plx87xx/0000:05:00.0/dump_regs_single
echo '=== HOST alm first empty-ish ==='
head -3 /sys/kernel/debug/plx87xx/0000:05:00.0/alm_state
echo '=== GUEST debugfs ==='
$SSH root@172.31.1.1 'ls /sys/kernel/debug/plx87xx/; echo --- alm ---; cat /sys/kernel/debug/plx87xx/0000:01:00.0/alm_state | head -5
echo --- BAR2_AT ---
grep -E "\[0x0c3c\]|\[0x0c40\]" /sys/kernel/debug/plx87xx/0000:01:00.0/dump_regs_single
echo --- spad ---
cat /sys/kernel/debug/plx87xx/0000:01:00.0/spad
'
echo '=== ping ==='
ping -c 1 -W 1 172.31.1.1
