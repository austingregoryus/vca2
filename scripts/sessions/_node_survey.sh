#!/bin/bash
# SPDX-License-Identifier: MIT
echo "=== identity ==="
hostname; uname -a
head -8 /etc/os-release
echo
echo "=== cpu ==="
lscpu | egrep 'Model name|CPU\(s\)|Thread|Core|MHz|Architecture|Vendor'
echo "vmx count: $(grep -c vmx /proc/cpuinfo)"
echo
echo "=== mem / disk ==="
free -h
df -hT
lsblk
echo
echo "=== gpu / drm ==="
lspci | grep -Ei 'vga|display|graphics|audio' || true
echo "--- /dev/dri ---"
ls -la /dev/dri 2>/dev/null || echo "no /dev/dri"
echo "--- i915 ---"
dmesg | grep -Ei 'i915|drm' | tail -20
echo
echo "=== opencl / vaapi ==="
command -v clinfo; command -v vainfo; command -v intel_gpu_top
clinfo 2>/dev/null | head -50
vainfo 2>/dev/null | head -25
ls /usr/lib/*/libOpenCL* /opt/intel 2>/dev/null | head
echo
echo "=== net ==="
ip -br addr
ip route
echo
echo "=== kvm / virt / containers ==="
ls -l /dev/kvm 2>/dev/null || echo "no /dev/kvm"
kvm-ok 2>/dev/null || true
systemctl is-active libvirtd 2>/dev/null || echo "libvirtd inactive"
virsh list --all 2>/dev/null || true
command -v docker; command -v lxc; command -v qemu-system-x86_64
dpkg -l | awk '/qemu|libvirt|docker|opencl|beignet|intel-opencl|media-sdk|i965|va-driver/{print $2,$3}' | head -50
echo
echo "=== vca guest modules ==="
lsmod | grep -Ei 'vca|vop|virtio|i915|kvm' | sort
echo
echo "=== pci on node ==="
lspci -nn | head -40
echo
echo "=== failed units ==="
systemctl --failed --no-pager
echo
echo "=== listening ==="
ss -lntp 2>/dev/null | head -25
echo
echo "=== apt holds / upgradable ==="
apt-mark showhold 2>/dev/null
apt-get -s upgrade 2>/dev/null | grep -E 'upgraded|held' | head
echo
echo "=== vca tools on node ==="
ls /usr/sbin/vca* /usr/bin/vca* 2>/dev/null
lsmod | head
echo
echo "=== sysfs vca ==="
ls /sys/class/vca* /sys/bus/vca* /sys/class/vcablk* 2>/dev/null
ls /dev/vca* /dev/vcablk* 2>/dev/null
echo
echo "=== iperf3 / stress ==="
command -v iperf3; command -v stress-ng; command -v mpstat
