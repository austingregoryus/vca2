#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== inter-node ping from node0 ==='
$SSH root@172.31.1.1 'echo to-node1; ping -c 2 -W 1 172.31.2.1; echo to-node2; ping -c 2 -W 1 172.31.3.1; echo to-wan; ping -c 1 -W 2 1.1.1.1'

echo
echo '=== node0 GPU/media/dev ==='
$SSH root@172.31.1.1 'echo --- vainfo ---; vainfo 2>&1 | head -40; echo --- /opt/intel ---; find /opt/intel -maxdepth 3 -type d; echo --- pkgs ---; dpkg -l | awk "/va-driver|libva|beignet|opencl|intel-media|mfx|mediasdk|ocl/{print \$2,\$3}"; echo --- /dev/vca ---; ls -l /dev/vca; ls -la /sys/class/vca/vca/; echo --- alpine ---; virsh list --all; virsh dominfo alpine-test; virsh vcpuinfo alpine-test | head; virsh domifaddr alpine-test'

echo
echo '=== node1 extras ==='
$SSH root@172.31.2.1 'hostname; df -h /; ls /dev/dri; ls /opt/intel; dpkg -l | awk "/qemu|libvirt|docker/{print \$2,\$3}"; command -v virsh; ls -l /dev/kvm /dev/vca'

echo
echo '=== node2 extras ==='
$SSH root@172.31.3.1 'hostname; df -h /; ls /dev/dri; ls /opt/intel; dpkg -l | awk "/qemu|libvirt|docker/{print \$2,\$3}"; command -v virsh; ls -l /dev/kvm /dev/vca'

echo
echo '=== host debugfs ==='
ls /sys/kernel/debug 2>/dev/null | grep -Ei 'vop|plx|vca|dma' || true
ls -la /sys/kernel/debug/vop* /sys/kernel/debug/plx* /sys/kernel/debug/vca* 2>/dev/null || true

echo
echo '=== host helpers / autoboot ==='
ls -la /usr/local/sbin/vca* 2>/dev/null || true
systemctl is-enabled vca2-autoboot.service 2>/dev/null || true
systemctl is-active vca2-autoboot.service 2>/dev/null || true
command -v iperf3 || true

echo
echo '=== host BAR sizes ==='
for d in 0000:05:00.0 0000:07:00.0 0000:0a:00.0; do
  echo "-- $d --"
  cat /sys/bus/pci/devices/$d/resource | awk 'NR==1 || NR==3 {print}'
  echo -n 'driver: '; basename "$(readlink -f /sys/bus/pci/devices/$d/driver 2>/dev/null)" 2>/dev/null
done
