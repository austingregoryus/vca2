#!/bin/bash
# SPDX-License-Identifier: MIT
# Apply the host-supplied network configuration from VCA sysfs.
#
# Xenial did this from a udev RUN+= rule. Modern systemd-udevd sandboxes and
# time-limits RUN programs, so the stock rule fires but exits 1 and the node
# sits at net_device_no_ip forever:
#
#   systemd-udevd[497]: eth0: Process '/sbin/vca_eth_cfg.sh eth0' failed with
#                       exit code 1.
#
# The FIRST version of this unit only waited for net_config to EXIST. That is
# racy: the file can be present but not yet populated by the host, in which
# case we run an empty script, succeed, and -- because of RemainAfterExit --
# never retry. It worked on roughly one boot in three, which is the worst
# possible failure rate because it looks like it works.
#
# So: retry until the interface actually has an IP, which is the real success
# condition, and re-read net_config on every attempt.
#
# Logs to /var/log/vca-netcfg.log on purpose. The host exposes no serial
# console, so if bring-up fails, mounting the image afterwards and reading
# this file is the ONLY way to find out why.
LOG=/var/log/vca-netcfg.log
exec >>"$LOG" 2>&1

CFG=/sys/class/vca/vca/net_config
SYS=/sys/class/vca/vca/sys_config
IFACE="${IFACE:-eth0}"
TRIES="${TRIES:-40}"

echo "=== $(date) vca-netcfg starting (iface=$IFACE) ==="

have_ip() { ip -4 addr show dev "$IFACE" 2>/dev/null | grep -q "inet "; }

for i in $(seq 1 "$TRIES"); do
    if have_ip; then
        echo "[$i] $IFACE already has an IP; done"
        break
    fi
    if [ ! -e "$CFG" ]; then
        echo "[$i] $CFG not present yet"
        sleep 2; continue
    fi
    # Present but possibly not yet populated by the host.
    if [ ! -s "$CFG" ] || ! grep -q "ip addr" "$CFG" 2>/dev/null; then
        echo "[$i] $CFG present but not populated yet"
        sleep 2; continue
    fi
    echo "[$i] applying net_config:"
    cat "$CFG"
    bash "$CFG"
    echo "[$i] apply rc=$?"
    sleep 2
    if have_ip; then
        echo "[$i] SUCCESS: $(ip -br addr show dev $IFACE)"
        break
    fi
    echo "[$i] no IP yet, retrying"
    sleep 2
done

if have_ip; then
    [ -e "$SYS" ] && { echo "running sys_config"; bash "$SYS"; }
else
    echo "FAILED: $IFACE never got an IP after $TRIES attempts"
    ip -br addr
fi
echo "=== done: $(ip -br addr show dev $IFACE 2>/dev/null) ==="
