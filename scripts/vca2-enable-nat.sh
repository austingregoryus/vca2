#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

VCA_NET="172.31.0.0/16"
OUT_IF="vmbr0"
SYSCTL_FILE="/etc/sysctl.d/99-vca-forwarding.conf"

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

if ! ip link show "$OUT_IF" >/dev/null 2>&1; then
  echo "Outbound interface $OUT_IF does not exist. Edit OUT_IF in this script." >&2
  exit 1
fi

cat > "$SYSCTL_FILE" <<EOF2
net.ipv4.ip_forward=1
EOF2
sysctl -w net.ipv4.ip_forward=1 >/dev/null

iptables -t nat -C POSTROUTING -s "$VCA_NET" -o "$OUT_IF" -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s "$VCA_NET" -o "$OUT_IF" -j MASQUERADE

iptables -C FORWARD -s "$VCA_NET" -o "$OUT_IF" -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -s "$VCA_NET" -o "$OUT_IF" -j ACCEPT

iptables -C FORWARD -d "$VCA_NET" -i "$OUT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || \
  iptables -A FORWARD -d "$VCA_NET" -i "$OUT_IF" -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT

if command -v netfilter-persistent >/dev/null 2>&1; then
  netfilter-persistent save
  echo "Saved rules with netfilter-persistent."
else
  echo "Rules are active but not yet persisted."
  echo "Install iptables-persistent, then run: netfilter-persistent save"
fi

iptables -t nat -L POSTROUTING -n -v
iptables -L FORWARD -n -v
