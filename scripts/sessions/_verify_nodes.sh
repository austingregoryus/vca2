#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
command -v sshpass >/dev/null || apt-get install -y sshpass >/dev/null

echo '=== forwarding/nat ==='
sysctl net.ipv4.ip_forward
iptables -t nat -L POSTROUTING -n | head -10

echo
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "========== $ip =========="
  $SSH root@$ip 'hostname; uptime; mount | grep vcablk; dmesg | grep -Ei "ext4|fsck|recover|error" | tail -15; echo --- failed ---; systemctl --failed --no-pager; echo --- kvm ---; ls -l /dev/kvm /dev/dri 2>/dev/null; virsh list --all 2>/dev/null | head'
  echo
done
