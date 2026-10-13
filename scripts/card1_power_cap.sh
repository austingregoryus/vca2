#!/bin/bash
# SPDX-License-Identifier: MIT
# Run on the Proxmox host:  bash scripts/card1_power_cap.sh
# Card 1 runs hot on 2x80 mm fans: cap package power on its live nodes.
# PL1 45 -> 35 W, PL2 56.25 -> 40 W. Persistent via a unit on each node's disk.
set -u
PL1=35000000; PL2=40000000
UNIT='[Unit]
Description=Cap package power (card 1 cooling is 2x80 mm; see VCA2 docs/THERMAL.md)
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c "echo PL1_ > /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw; echo PL2_ > /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw"

[Install]
WantedBy=multi-user.target'
UNIT="${UNIT/PL1_/$PL1}"; UNIT="${UNIT/PL2_/$PL2}"
# Card 1 nodes; 172.31.4.1 is out (dead since 2026-09-28) -- add it back if it returns.
for ip in ${CARD1_NODES:-172.31.5.1 172.31.6.1}; do
  SSHPASS="${NODE_PW:-vista1}" sshpass -e ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$ip \
    "cat > /etc/systemd/system/rapl-cap.service && systemctl daemon-reload && systemctl enable --now rapl-cap.service >/dev/null 2>&1; systemctl restart rapl-cap.service; printf '%s PL1=%s PL2=%s %s\n' \$(hostname) \$(cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw) \$(cat /sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw) \$(systemctl is-enabled rapl-cap)" <<<"$UNIT"
done
