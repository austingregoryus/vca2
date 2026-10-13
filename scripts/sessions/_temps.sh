#!/bin/bash
# SPDX-License-Identifier: MIT
# Safe: vcactl temp only.
echo "=== $(date -Is) ==="
vcactl temp 2>/dev/null || vcactl --skip-modprobe-check temp
echo
vcactl --skip-modprobe-check status 2>/dev/null | head -5
