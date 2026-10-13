#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Clean poweroff of every node on both cards, then detach their disk images.
# ExecStop of vca2-card.service, so it runs on every host shutdown/reboot.
#
# Without this the nodes lose power with their ext4 images mounted RW -- the
# journal recovers, but it is the same as pulling the plug. Intel's
# vcactl.service also sends os-shutdown on stop, but with a 10 s timeout.
set -u
NODE_PW="${NODE_PW:-vista1}"
log() { printf '[vca-shutdown] %s\n' "$*"; }

# Nodes that are attached right now: "card cpu" pairs from blockio list.
ATTACHED="$(vcactl blockio list 2>/dev/null | awk '$3=="vcablk0" && $4=="active" {print $1, $2}')"
[ -n "$ATTACHED" ] || ATTACHED="0 0
0 1
0 2"
want=$(printf '%s
' "$ATTACHED" | grep -c .)

while read -r card cpu; do
  i=$((card * 3 + cpu + 1))
  sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
    "172.31.$i.1" 'sync; systemctl poweroff' >/dev/null 2>&1 &
done <<< "$ATTACHED"
wait
log "poweroff sent to $want nodes"

for _ in $(seq 1 40); do
  n=0
  while read -r card cpu; do
    vcactl status "$card" "$cpu" 2>/dev/null | grep -q 'power_off' && n=$((n + 1))
  done <<< "$ATTACHED"
  [ "$n" = "$want" ] && break
  sleep 5
done
vcactl status 2>/dev/null | grep Cpu | sed 's/^/[vca-shutdown] /'

while read -r card cpu; do
  vcactl blockio close "$card" "$cpu" vcablk0 >/dev/null 2>&1 || true
done <<< "$ATTACHED"
sync
log "images detached"
exit 0
